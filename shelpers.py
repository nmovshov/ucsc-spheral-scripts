#-------------------------------------------------------------------------------
#   Spheral Helpers - A collection of some convenience functions for reuse in
#                     the planetary collision scripts.
#
# Author: nmovshov at gmail dot com
#-------------------------------------------------------------------------------
import sys, os
import mpi # Mike's simplified mpi wrapper
import cPickle as pickle
import SolidSpheral3d as sph

def construct_eos_for_material(material_tag,units,etamin=0.94,etamax=100.0):
    """Return a spheral EOS object for a material identified by tag.

    See also: material_dictionary
    """

    # Make sure we are not wasting our time.
    assert material_tag in material_dictionary.keys()
    assert isinstance(units,sph.PhysicalConstants)
    assert isinstance(etamin,float)
    assert isinstance(etamax,float)

    # Build eos using our internal dictionary
    mat_dict = material_dictionary[material_tag]
    eos_constructor = mat_dict['eos_constructor']
    eos_arguments = mat_dict['eos_arguments']
    eos = None

    if mat_dict['eos_type'] == 'tillotson':
        eos = eos_constructor(eos_arguments['materialName'],
                              etamin, etamax, units)
        pass
    else:
        print "EOS type {} not yet implemented".format(mat_dict['eos_type'])
        pass

    # And Bob's our uncle
    return eos
    # End function construct_eos_for_material

def spickle_node_list(nl,filename=None,silent=False):
    """Pack physical field variables from a node list in a dict and pickle.

    (Note: This is not a true pickler class.)

    spickle_node_list(nl,filename) extracts field variables from all nodes of nl,
    which must be a valid node list, and packs them in a dict that is returned
    to the caller. If the optional argument filename is a string then dict will
    also be pickled to a file of that name. The file will be overwritten if it
    exists.

    The s in spickle is for 'serial', a reminder that this method collects all
    nodes of the node list (from all ranks) in a single process. Thus this method
    is mainly useful for interactive work with small node lists. It is the user's
    responsibility to make sure her process has enough memory to hold the returned
    dict.

    See also: pflatten_node_list
    """

    # Make sure we are not wasting our time.
    assert isinstance(nl,(sph.Spheral.NodeSpace.FluidNodeList3d,
                          sph.Spheral.SolidMaterial.SolidNodeList3d)
                      ), "argument 1 must be a node list"
    assert isinstance(silent, bool), "true or false"
    
    # Start collecting data.
    if not silent:
        sys.stdout.write('Pickling ' +  nl.label() + ' ' + nl.name + '........')

    # Get values of field variables stored in internal nodes.
    xloc = nl.positions().internalValues()
    vloc = nl.velocity().internalValues()
    mloc = nl.mass().internalValues()
    rloc = nl.massDensity().internalValues()
    uloc = nl.specificThermalEnergy().internalValues()
    Hloc = nl.Hfield().internalValues()
    #(pressure and temperature are stored in the eos object.)
    eos = nl.equationOfState()
    ploc = sph.ScalarField('ploc',nl)
    Tloc = sph.ScalarField('loc',nl)
    rref = nl.massDensity()
    uref = nl.specificThermalEnergy()
    eos.setPressure(ploc,rref,uref)
    eos.setTemperature(Tloc,rref,uref)

    # Zip fields so that we have all fields for each node in the same tuple.
    #  We do this so we can concatenate everything in a single reduction operation,
    #  to ensure that all fields in one record in the final list belong to the same
    #  node.
    localFields = zip(xloc, vloc, mloc, rloc, uloc, ploc, Tloc, Hloc)

    # Do a SUM reduction on all ranks.
    #  This works because the + operator for python lists is a concatenation!
    globalFields = mpi.allreduce(localFields, mpi.SUM)

    # Create a dictionary to hold field variables.
    nlFieldDict = dict(name=nl.name,
                       x=[],   # position vector
                       v=[],   # velocity vector
                       m=[],   # mass
                       rho=[], # mass density
                       p=[],   # pressure
                       h=[],   # smoothing ellipsoid axes
                       T=[],   # temperature
                       U=[],   # specific thermal energy
                      )

    # Loop over nodes to fill field values.
    nbGlobalNodes = mpi.allreduce(nl.numInternalNodes, mpi.SUM)
    for k in range(nbGlobalNodes):
        nlFieldDict[  'x'].append((globalFields[k][0].x, globalFields[k][0].y, globalFields[k][0].z))
        nlFieldDict[  'v'].append((globalFields[k][1].x, globalFields[k][1].y, globalFields[k][1].z))
        nlFieldDict[  'm'].append( globalFields[k][2])
        nlFieldDict['rho'].append( globalFields[k][3])
        nlFieldDict[  'U'].append( globalFields[k][4])
        nlFieldDict[  'p'].append( globalFields[k][5])
        nlFieldDict[  'T'].append( globalFields[k][6])
        nlFieldDict[  'h'].append((globalFields[k][7].Inverse().eigenValues().x,
                                   globalFields[k][7].Inverse().eigenValues().y,
                                   globalFields[k][7].Inverse().eigenValues().z,
                                   ))

    # Optionally, pickle the dict to a file.
    if mpi.rank == 0:
        if filename is not None:
            if isinstance(filename, str):
                with open(filename, 'wb') as fid:
                    pickle.dump(nlFieldDict, fid)
                    pass
                pass
            else:
                msg = "Dict NOT pickled to file because " + \
                      "argument 2 is {} instead of {}".format(type(filename), type('x'))
                sys.stderr.write(msg+'\n')
                pass
            pass
        pass
        
    # And Bob's our uncle.
    if not silent:
        print "Done."
    return nlFieldDict
    # End function spickle_node_list


def pflatten_node_list(nl,filename,do_header=True,nl_id=0,silent=False):
    """Flatten physical field values from a node list to a rectangular ascii file.

    pflatten_node_list(nl,filename) extracts field variables from all nodes of nl,
    which must be a valid node list, and writes them as a rectangular table into
    the text file filename. (A short header is also written, using the # comment
    character so the resulting file can be easily read with, e.g., numpy.loadtext.)
    The file will be overwritten if it exists.

    pflatten_node_list(...,do_header=False) omits the header and appends the flattened
    nl to the end of the file if one exists.

    pflatten_node_list(...,nl_id=id) places the integer id in the first column
    of every node (row) in the node list. This can be used when appending multiple
    lists to the same file, providing a convenient way to distinguish nodes from
    different lists when the file is later read. The default id (for single node
    list files) is 0.

    The format of the output table is (one line per node):
      id x y z vx vy vz m rho p T U hmin hmax

    The p in pflatten is for 'parallel', a reminder that all nodes will be
    processed in their local rank, without ever being communicated or collected
    in a single process. Each mpi rank will wait its turn to access the output file,
    so the writing is in fact serial, but avoids bandwidth and memory waste and
    is thus suitable for large node lists from high-res runs.

    See also: spickle_node_list
    """

    # Make sure we are not wasting our time.
    assert isinstance(nl,(sph.Spheral.NodeSpace.FluidNodeList3d,
                          sph.Spheral.SolidMaterial.SolidNodeList3d)
                      ), "argument 1 must be a node list"
    assert isinstance(filename, str), "argument 2 must be a simple string"
    assert isinstance(do_header, bool), "true or false"
    assert isinstance(silent, bool), "true or false"
    assert isinstance(nl_id, int), "int only idents"
    assert not isinstance(nl_id, bool), "int only idents"

    # Write the header.
    if do_header:
        nbGlobalNodes = mpi.allreduce(nl.numInternalNodes, mpi.SUM)
        header = header_template.format(nbGlobalNodes)
        if mpi.rank == 0:
            fid = open(filename,'w')
            fid.write(header)
            fid.close()
            pass
        pass
     
    # Start collecting data.
    if not silent:
        sys.stdout.write('Flattening ' + nl.label() + ' ' + nl.name + '........')
    
    # Get values of field variables stored in internal nodes.
    xloc = nl.positions().internalValues()
    vloc = nl.velocity().internalValues()
    mloc = nl.mass().internalValues()
    rloc = nl.massDensity().internalValues()
    uloc = nl.specificThermalEnergy().internalValues()
    Hloc = nl.Hfield().internalValues()
    #(pressure and temperature are stored in the eos object.)
    eos = nl.equationOfState()
    ploc = sph.ScalarField('ploc',nl)
    Tloc = sph.ScalarField('loc',nl)
    rref = nl.massDensity()
    uref = nl.specificThermalEnergy()
    eos.setPressure(ploc,rref,uref)
    eos.setTemperature(Tloc,rref,uref)

    # Procs take turns writing internal node values to file.
    for proc in range(mpi.procs):
        if proc == mpi.rank:
            fid = open(filename,'a')
            for nk in range(nl.numInternalNodes):
                line  = "{:2d}  ".format(nl_id)
                line += "{0.x:+12.5e}  {0.y:+12.5e}  {0.z:+12.5e}  ".format(xloc[nk])
                line += "{0.x:+12.5e}  {0.y:+12.5e}  {0.z:+12.5e}  ".format(vloc[nk])
                line += "{0:+12.5e}  ".format(mloc[nk])
                line += "{0:+12.5e}  ".format(rloc[nk])
                line += "{0:+12.5e}  ".format(ploc[nk])
                line += "{0:+12.5e}  ".format(Tloc[nk])
                line += "{0:+12.5e}  ".format(uloc[nk])
                line += "{0:+12.5e}  ".format(Hloc[nk].Inverse().eigenValues().minElement())
                line += "{0:+12.5e}  ".format(Hloc[nk].Inverse().eigenValues().maxElement())
                line += "\n"
                fid.write(line)
                pass
            fid.close()
            pass
        mpi.barrier()
        pass
     
    # And Bob's our uncle.
    if not silent:
        print "Done."
    # End function pflatten_node_list


def pflatten_node_list_list(nls,filename,do_header=True,silent=False):
    """Flatten a list of node lists to a rectangular ascii file.

    pflatten_node_list_list(nls,filename) writes meta data about the node lists
    in nls, which must be either a list or a tuple of valid node lists, to a header
    of the file filename, and then calls pflatten_node_list(nl,filename) for each
    nl in nls.

    pflatten_node_list_list(...,do_header=False) omits the header.

    See also: pflatten_node_list
    """

    # Make sure we are not wasting our time.
    assert isinstance(nls,(list,tuple)), "argument 1 must be a list or tuple"
    assert isinstance(filename, str), "argument 2 must be a simple string"
    assert isinstance(do_header, bool), "true or false"
    assert isinstance(silent, bool), "true or false"
    for nl in nls:
        assert isinstance(nl,(sph.Spheral.NodeSpace.FluidNodeList3d,
                                  sph.Spheral.SolidMaterial.SolidNodeList3d)
                         ), "argument 1 must contain node lists"

    # Write the header.
    if do_header:
        nbGlobalNodes = 0
        for nl in nls:
            nbGlobalNodes += mpi.allreduce(nl.numInternalNodes, mpi.SUM)
        header = header_template.format(nbGlobalNodes)
        if mpi.rank == 0:
            fid = open(filename,'w')
            fid.write(header)
            fid.close()
            pass
        pass

    # Send contents of nls to be flattened.
    for k in range(len(nls)):
        pflatten_node_list(nls[k],filename,do_header=False,nl_id=k,silent=silent)
        pass

    # And Bob's our uncle.
    if not silent:
        print "Done."
    # End function pflatten_node_list_list


global header_template
header_template = """
################################################################################
# This file contains output data from a Spheral++ simulation, including all 
# field variables as well as some diagnostic data and node meta data. This
# file should contain {} data lines, one per SPH node used in the simulation.
# Line order is not significant and is not guaranteed to match the node ordering
# during the run, which itself is not significant. The columns contain field
# values in whatever units where used in the simulation. Usually MKS.
# Columns are:
#    | id | x | y | z | vx | vy | vz | m | rho | p | T | U | hmin | hmax |
#
# Column legend:
#    
#        id - an integer identifier of the node list this node came from
#     x,y,z - node space coordinates 
#  vx,vy,vz - node velocity components
#         m - node mass
#       rho - mass density
#         p - pressure
#         T - temperature
#         U - specific internal energy
# hmin,hmax - smallest and largest half-axes of the smoothing ellipsoid 
#
# Tip: load table into python with np.load()
#
################################################################################
"""

global material_dictionary
# A dictionary of unique short tags for commonly used material EOSs
material_dictionary = {}

material_dictionary['h2oice'] = dict(
        eos_type = 'tillotson',
        eos_constructor = sph.TillotsonEquationOfState,
        eos_arguments = {'materialName':'pure ice'},
        eos_id = len(material_dictionary.keys()) + 1,
        )

material_dictionary['dirtyice'] = dict(
        eos_type = 'tillotson',
        eos_constructor = sph.TillotsonEquationOfState,
        eos_arguments = {'materialName':'30% silicate ice'},
        eos_id = len(material_dictionary.keys()) + 1,
        )





