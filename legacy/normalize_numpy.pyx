#normalize numpy array


cimport numpy as cnp
import numpy as np


def normalize(array:np.ndarray):
    cdef double ratio = 2/(np.max(array)-np.min(array)) 
    cdef double shift = (np.max(array)+np.min(array))/2

    cdef float[:,:] mv_normalized_array 
    mv_normalized_array = (array - shift)*ratio
    normalized_array = np.ndarray(dtype=np.float32, shape=array.shape, buffer=mv_normalized_array)

    return normalized_array