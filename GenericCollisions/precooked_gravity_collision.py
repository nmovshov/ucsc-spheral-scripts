#! /proj/nmovshov_hindmost/collisions/SPHERAL/bin/python
#-------------------------------------------------------------------------------
# first stab at loading pre-built planets. this is a rushed job to be fixed after
# agu abstract is due
#-------------------------------------------------------------------------------
from math import *
import sys, os
import random
import mpi # Mike's simplified mpi wrapper
import shelpers # My module of some helper functions
from SolidSpheral3d import *
from findLastRestart import *
from VoronoiDistributeNodes import distributeNodes3d
from NodeHistory import NodeHistory
from GenerateNodeDistribution3d import GenerateNodeDistribution3d

jobName = 'precooked'
jobDesc = "load and run precooked planet(s)."
print '\n', jobName.upper(), '-', jobDesc.upper()

# load precooked lists
matCore = 'basalt'
matMantle = 'pure ice'

mFile = 'target_core.pnl'
fid = open(mFile,'rb')
targetCoreData = shelpers.pickle.load(fid)
targetCore_pos = targetCoreData['x']
targetCore_rho = targetCoreData['rho']
targetCore_m   = targetCoreData['m']
targetCore_u   = targetCoreData['U']
targetCore_r   = [sqrt(x[0]**2 + x[1]**2 + x[2]**2) for x in targetCore_pos]
targetCore_nbNodes = len(targetCore_rho)
fid.close()

mFile = 'target_mantle.pnl'
fid = open(mFile,'rb')
targetMantleData = shelpers.pickle.load(fid)
targetMantle_pos = targetMantleData['x']
targetMantle_rho = targetMantleData['rho']
targetMantle_m   = targetMantleData['m']
targetMantle_u   = targetMantleData['U']
targetMantle_r   = [sqrt(x[0]**2 + x[1]**2 + x[2]**2) for x in targetMantle_pos]
targetMantle_nbNodes = len(targetMantle_rho)
fid.close()

rPlanet = max(targetMantle_r)
rhoLower = min(targetMantle_rho)
rhoUpper = max(targetCore_rho)
simGlobalNodes = targetCore_nbNodes + targetMantle_nbNodes

# Times, simulation control, and output
steps = None              # None or advance a number of steps rather than to a time
goalTime = 100           # Time to advance to (sec)
dt = 2                    # Initial guess for time step (sec)
vizTime = 200             # Time frequency for dropping viz files (sec)
vizCycle = 1           # Cycle frequency for dropping viz files

# Node seeding parameters ("resolution")
nPerh = 1.51              # Nominal number of nodes per smoothing scale
hmin = 1.0e-6*rPlanet     # Lower bound on smoothing length
hmax = 1.0e-1*rPlanet     # Upper bound on smoothing length
rhomin = 0.01*rhoLower    # Lower bound on node density
rhomax = 4.*rhoUpper     # Upper bound on node density

# Gravity parameters
softLength = 1.0e-6       # Fraction of planet radius as softening length
opening = 1.0             # Dimensionless opening parameter for gravity tree walk
fdt = 0.1                 # Gravity timestep multiplier
softLength *= rPlanet
G = MKS().G

# More simulation parameters
dtGrowth = 2.0            # Maximum growth factor for time step in a cycle (dimensionless)
dtMin = 2                 # Minimum allowed time step (sec)
dtMax = 1000.0*dt         # Maximum allowed time step (sec)
verbosedt = True         # Verbose reporting of the time step criteria per cycle
maxSteps = 1000           # Maximum allowed steps for simulation advance
statsStep = None          # Frequency for sampling conservation statistics and such
redistributeStep = 2000   # Frequency to load balance problem from scratch
restartStep = 100         # Frequency to drop restart files
restoreCycle = None       # If None, latest available restart cycle is selected
baseDir = jobName         # Base name for directory to store output in

#-------------------------------------------------------------------------------
# NAV Spheral hydro solver options
# These options for spheral's hydro mechanism are normally left alone.
#-------------------------------------------------------------------------------
HydroConstructor = ASPHHydro
Qconstructor = MonaghanGingoldViscosity
Cl = 1.0
Cq = 1.0
Qlimiter = False
balsaraCorrection = False
epsilon2 = 1e-2
negligibleSoundSpeed = 1e-4 
csMultiplier = 1e-4
hminratio = 0.1
limitIdealH = False
cfl = 0.5
useVelocityMagnitudeForDt = False
XSPH = True
epsilonTensile = 0.3
nTensile = 4
HEvolution = IdealH
densityUpdate = IntegrateDensity # Sum is best for fluids, integrate for solids
compatibleEnergyEvolution = True
rigorousBoundaries = False

#-------------------------------------------------------------------------------
# NAV Equations of state
# Here we construct an eos object for each node list. In this case, one fore core,
# one for mantle. The choice of eos is determined by the material string. See
# ../MATERIALS.md for the available options.
# TODO: fix ANEOS, currently only tillotson works.
#-------------------------------------------------------------------------------
eosCore, eosMantle = None, None
# Most eos constructors take a units object, we usually use MKS
units = PhysicalConstants(1.0,   # Unit length in meters
                          1.0,   # Unit mass in kg
                          1.0)   # Unit time in seconds

# Tillotson EOS for many geologic materials
mats = ['granite', 'basalt', 'nylon', 'pure ice', '30% silicate ice', 'water']
etamin, etamax = 0.01, 100.0
if matMantle.lower() in mats:
    eosMantle = TillotsonEquationOfState(matMantle,etamin,etamax,units)
if matCore.lower() in mats:
    eosCore = TillotsonEquationOfState(matCore,etamin,etamax,units)

# Verify valid EOSs (currently only tillotson works)
if eosCore is None or eosMantle is None:
    raise ValueError("invalid material selection for core and/or mantle")
if not (eosCore.valid() and eosMantle.valid()):
    raise ValueError("core and/or mantle eos construction failed")

#-------------------------------------------------------------------------------
# NAV Restarts and output directories
# Here we create the output directories, and deal with restarted runs if any.
#-------------------------------------------------------------------------------
# Restart and output files.
jobDir = os.path.join(baseDir, 
                       'nxPlanet=%d' % simGlobalNodes,
                       )
restartDir = os.path.join(jobDir, 'restarts', 'proc-%04d' % mpi.rank)
vizDir = os.path.join(jobDir, 'viz')
outDir = os.path.join(jobDir, 'output')
restartName = os.path.join(restartDir, jobName)

# Check if the necessary directories exist.  If not, create them.
if mpi.rank == 0:
    if not os.path.exists(jobDir):
        os.makedirs(jobDir)
    if not os.path.exists(vizDir):
        os.makedirs(vizDir)
    if not os.path.exists(restartDir):
        os.makedirs(restartDir)
    if not os.path.exists(outDir):
        os.makedirs(outDir)
mpi.barrier()
if not os.path.exists(restartDir):
    os.makedirs(restartDir)
mpi.barrier()

# If we're restarting, find the set of most recent restart files.
if restoreCycle is None:
    restoreCycle = findLastRestart(restartName)



# Create the node lists.
targetCore   = makeFluidNodeList('target_core', eosCore, 
                           nPerh = nPerh, 
                           hmin = hmin,
                           hmax = hmax,
                           rhoMin = rhomin,
                           rhoMax = rhomax,
                           )

targetMantle = makeFluidNodeList('target_mantle', eosMantle, 
                           nPerh = nPerh, 
                           hmin = hmin,
                           hmax = hmax,
                           rhoMin = rhomin,
                           rhoMax = rhomax,
                           )

nodeSet = [targetCore,targetMantle]

# Unless restarting, create the generators and set initial field values.
if restoreCycle is None:
    # Start with the stock generator.
    targetCoreGenerator   = GenerateNodeDistribution3d(1, 1, targetCore_nbNodes,
                                                 rhoUpper,
                                                 distributionType = 'line',
                                                 nNodePerh = nPerh)
    assert targetCoreGenerator.localNumNodes() == targetCore_nbNodes

    targetMantleGenerator = GenerateNodeDistribution3d(1, 1, targetMantle_nbNodes,
                                                 rhoUpper,
                                                 distributionType = 'line',
                                                 nNodePerh = nPerh)
    assert targetMantleGenerator.localNumNodes() == targetMantle_nbNodes
     
    # Substitute values from pre-cooked data (except density)
    for k in range(targetCore_nbNodes):
        targetCoreGenerator.x[k] = targetCore_pos[k][0]
        targetCoreGenerator.y[k] = targetCore_pos[k][1]
        targetCoreGenerator.z[k] = targetCore_pos[k][2]
        targetCoreGenerator.m[k] = targetCore_m[k]
    pass

    for k in range(targetMantle_nbNodes):
        targetMantleGenerator.x[k] = targetMantle_pos[k][0]
        targetMantleGenerator.y[k] = targetMantle_pos[k][1]
        targetMantleGenerator.z[k] = targetMantle_pos[k][2]
        targetMantleGenerator.m[k] = targetMantle_m[k]
    pass

   # Fill node lists using generators and distribute to ranks.
    print "Starting node distribution..."
    distributeNodes3d((targetCore, targetCoreGenerator),
                      (targetMantle, targetMantleGenerator)
                      )
     
    nGlobalNodes = 0
    for n in nodeSet:
        print "Generator info for %s" % n.name
        print "   Minimum number of nodes per domain : ", \
              mpi.allreduce(n.numInternalNodes, mpi.MIN)
        print "   Maximum number of nodes per domain : ", \
              mpi.allreduce(n.numInternalNodes, mpi.MAX)
        print "               Global number of nodes : ", \
              mpi.allreduce(n.numInternalNodes, mpi.SUM)
        nGlobalNodes += mpi.allreduce(n.numInternalNodes, mpi.SUM)
    del n
    print "Total number of (internal) nodes in simulation: ", nGlobalNodes

    # Now fill in pre-cooked density and energy
    for k in range(targetCore_nbNodes):
        targetCore.massDensity()[k] = targetCore_rho[k]
        targetCore.specificThermalEnergy()[k] = targetCore_u[k]
    pass
    for k in range(targetMantle_nbNodes):
        targetMantle.massDensity()[k] = targetMantle_rho[k]
        targetMantle.specificThermalEnergy()[k] = targetMantle_u[k]
    pass
    
# The spheral controller needs a DataBase object to hold the node lists.
db = DataBase()
for n in nodeSet:
    db.appendNodeList(n)
del n

# Create the gravity package.
gravity = OctTreeGravity(G = G, 
                         softeningLength = softLength, 
                         opening = opening, 
                         ftimestep = fdt)

# Create the kernel functions for SPH.
WT = TableKernel(BSplineKernel(), 1000) # one for normal hydro
WTPi = WT                               # one for artificial viscosity

# Create the artificial viscosity object.
q = Qconstructor(Cl, Cq)
q.limiter = Qlimiter
q.balsaraShearCorrection = balsaraCorrection
q.epsilon2 = epsilon2
q.negligibleSoundSpeed = negligibleSoundSpeed
q.csMultiplier = csMultiplier

# Create the hydro package.
hydro = HydroConstructor(WT,
                         WTPi,
                         q,
                         cfl = cfl,
                         useVelocityMagnitudeForDt = useVelocityMagnitudeForDt,
                         compatibleEnergyEvolution = compatibleEnergyEvolution,
                         gradhCorrection = False,
                         densityUpdate = densityUpdate,
                         HUpdate = HEvolution,
                         XSPH = XSPH,
                         epsTensile = epsilonTensile,
                         nTensile = nTensile)

# Create the time integrator and attach the physics packages to it.
integrator = SynchronousRK2Integrator(db)
integrator.appendPhysicsPackage(gravity)
integrator.appendPhysicsPackage(hydro)
integrator.lastDt = dt
integrator.dtMin = dtMin
integrator.dtMax = dtMax
integrator.dtGrowth = dtGrowth
integrator.verbose = verbosedt
integrator.rigorousBoundaries = rigorousBoundaries

# Create the controller.
control = SpheralController(integrator, WT,
                            statsStep = statsStep,
                            restartStep = restartStep,
                            redistributeStep = redistributeStep,
                            restartBaseName = restartName,
                            restoreCycle = restoreCycle,
                            vizBaseName = jobName,
                            vizDir = vizDir,
                            vizStep = vizCycle,
                            vizTime = vizTime)


if not steps is None:
    control.step(steps)
else:
    control.advance(goalTime, maxSteps)
    control.dropRestartFile()
    control.dropViz()
pass
