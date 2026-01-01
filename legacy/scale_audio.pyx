#scale_audi

# from scipy.ndimage cimport zoom
# from scipy.signal import resample
import numpy as np
cimport numpy as npc

from resampy import resample

def scale_audio(npc.ndarray[npc.float32_t, ndim=2] track, int scale, int sample_rate ):
            
        # cdef cnp.npy_double[:,:] track
        # track = track_obj.track.audio_buffer.audio.astype(np.double).T
        
        cdef double target_rate
        # cdef cnp.npy_double[:,:] audio_level_array
        cdef double zoom_scale
        cdef int original_samples
        cdef int zoomed_samples

        original_samples = track.shape[1]

        target_rate = (1000/scale)*2
        zoom_scale = target_rate/sample_rate
        zoomed_samples = int(original_samples*zoom_scale)
        
        print('track shape before zoom:', track.shape[0], track.shape[1] )
        print('target zoom scale: ', zoom_scale)

        # audio_level_array = zoom(track, [1,zoom_scale], prefilter=True )
        # audio_level_array = resample(track, sample_rate, int(sample_rate*zoom_scale), 1)
        audio_level_array = resample(track, sample_rate, int(sample_rate*zoom_scale), axis=1)
 
        # print('audio level array shape:' , audio_level_array.shape)



        return audio_level_array