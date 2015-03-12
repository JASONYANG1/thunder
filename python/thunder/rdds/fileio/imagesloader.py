"""Provides ImagesLoader object and helpers, used to read Images data from disk or other filesystems.
"""
from matplotlib.pyplot import imread
from io import BytesIO
from numpy import array, dstack, frombuffer, ndarray, prod, load, swapaxes
from thunder.rdds.fileio.readers import getParallelReaderForPath
from thunder.rdds.images import Images


class ImagesLoader(object):
    """Loader object used to instantiate Images data stored in a variety of formats.
    """
    def __init__(self, sparkContext):
        """Initialize a new ImagesLoader object.

        Parameters
        ----------
        sparkcontext: SparkContext
            The pyspark SparkContext object used by the current Thunder environment.
        """
        self.sc = sparkContext

    def fromArrays(self, arrays):
        """Load Images data from passed sequence of numpy arrays.

        Expected usage is mainly in testing - having a full dataset loaded in memory
        on the driver is likely prohibitive in the use cases for which Thunder is intended.
        """
        # if passed a single array, cast it to a sequence of length 1
        if isinstance(arrays, ndarray):
            arrays = [arrays]

        shape = None
        dtype = None
        for ary in arrays:
            if shape is None:
                shape = ary.shape
                dtype = ary.dtype
            if not ary.shape == shape:
                raise ValueError("Arrays must all be of same shape; got both %s and %s" %
                                 (str(shape), str(ary.shape)))
            if not ary.dtype == dtype:
                raise ValueError("Arrays must all be of same data type; got both %s and %s" %
                                 (str(dtype), str(ary.dtype)))
        return Images(self.sc.parallelize(enumerate(arrays), len(arrays)),
                      dims=shape, dtype=str(dtype), nimages=len(arrays))

    def fromStack(self, dataPath, dims, dtype='int16', ext='stack', startIdx=None, stopIdx=None, recursive=False):
        """Load an Images object stored in a directory of flat binary files

        The RDD wrapped by the returned Images object will have a number of partitions equal to the number of image data
        files read in by this method.

        Currently all binary data read by this method is assumed to be formatted as signed 16 bit integers in native
        byte order.

        Parameters
        ----------

        dataPath: string
            Path to data files or directory, specified as either a local filesystem path or in a URI-like format,
            including scheme. A datapath argument may include a single '*' wildcard character in the filename.

        dims: tuple of positive int
            Dimensions of input image data, ordered with fastest-changing dimension first

        ext: string, optional, default "stack"
            Extension required on data files to be loaded.

        startIdx, stopIdx: nonnegative int. optional.
            Indices of the first and last-plus-one data file to load, relative to the sorted filenames matching
            `datapath` and `ext`. Interpreted according to python slice indexing conventions.

        recursive: boolean, default False
            If true, will recursively descend directories rooted at datapath, loading all files in the tree that
            have an extension matching 'ext'. Recursive loading is currently only implemented for local filesystems
            (not s3).
        """
        if not dims:
            raise ValueError("Image dimensions must be specified if loading from binary stack data")

        def toArray(buf):
            return frombuffer(buf, dtype=dtype, count=int(prod(dims))).reshape(dims, order='F')

        reader = getParallelReaderForPath(dataPath)(self.sc)
        readerRdd = reader.read(dataPath, ext=ext, startIdx=startIdx, stopIdx=stopIdx, recursive=recursive)
        return Images(readerRdd.mapValues(toArray), nimages=reader.lastNRecs, dims=dims,
                      dtype=dtype)

    def fromOCP (self, dataPath, resolution, serverName='ocp.me', startIdx=None, stopIdx=None, minBound=None, maxBound=None ):
      """Sets up a new Image object with data to read from OCP
      
        Parameters
        ----------
        
        dataPath: string
            Name of the token/bucket in OCP. You can use the token name you created in OCP here. You can also access publicly avaliable data on OCP at this URL "http://ocp.me/ocp/ca/public_tokens/"
        
        resolution: nonnegative int
            Resolution of the data in OCP

        serverName: string. optional.
            Name of the server in OCP which has the corresponding token. By default this is always ocp.me but if you have an alternate server, you can set it here.

        startIdx, stopIdx: nonnegative int. optional.
            Indices of the first and last-plus-one data file to load, relative to the sorted filenames matching `datapath` and `ext`. Interpreted according to python slice indexing conventions. In OCP this is the starttime and endtime of your data.

        minBound, maxBound: tuple of nonnegative int. optional.
            X,Y,Z bounds of the data you want to fetch from OCP. minBound contains the (xMin,yMin,zMin) while maxBound contains (xMax,yMax,zMax)
      """
      
      # Given a datapath/bucket Query JSON
      # Given bounds get a list of URI's
      import urllib2
      urlList=[]
      url = 'http://{}/ocp/ca/{}/info/'.format(serverName,dataPath)

      try:
        f = urllib2.urlopen ( url )
      except urllib2.URLError, e:
        raise Exception ( "Faile URL {}".format(url) )

      import json
      projInfo = json.loads ( f.read() )

      # Loading Information from JSON object
      ximageSize, yimageSize = projInfo['dataset']['imagesize']['{}'.format(resolution)]
      zimageStart, zimageStop = projInfo['dataset']['slicerange']
      timageStart, timageStop = projInfo['dataset']['timerange']
      xDim, yDim, zDim = cubeDims = projInfo['dataset']['cube_dimension']['{}'.format(resolution)]

      # Checking if dimensions are within bounds
      if startIdx == None:
        startIdx = timageStart
      elif startIdx < timageStart or startIdx > timageStop:
        raise Exception ( "startIdx out of bounds {},{}".format(timageStart,timageStop) )

      if stopIdx == None:
        stopIdx = timageStop
      elif stopIdx < timageStart or stopIdx > timageStop:
        raise Exception( "startIdx out of bounds {},{}".format(timageStart,timageStop) )

      if minBound == None:
        minBound = (0,0,zimageStart)
      elif minBound < (0,0,zimageStart) or minBound > (ximageSize,yimageSize,zimageStop):
        raise Exception ( "minBound is incorrect {},{}".format( (0,0,zimageStart), (ximageSize,yimageSize,zimageStop) ) )

      if maxBound == None:
        maxBound = (ximageSize,yimageSize,zimageStop)
      elif maxBound < (0,0,zimageStart) or maxBound > (ximageSize,yimageSize,zimageStop):
        raise Exception ( "minBound is incorrect {},{}".format( (0,0,zimageStart), (ximageSize,yimageSize,zimageStop) ) )

      for t in range(timageStart,timageStop,1):
        urlList.append( "http://{}/ocp/ca/{}/npz/{},{}/{}/{},{}/{},{}/{},{}/".format(serverName,dataPath,t,t+1,resolution,minBound[0],maxBound[0],minBound[1],maxBound[1],minBound[2],maxBound[2]) )
      

      def read (url):
        """Featch URL from the server"""

        try:
          f = urllib2.urlopen (url)
        except urllib2.URLError, e:
          print "Failed URL {}.".format(url)
          raise

        imgData = f.read()
        
        import zlib
        import cStringIO
        pageStr = zlib.decompress ( imgData[:] )
        pageObj = cStringIO.StringIO ( pageStr )
        data = load(pageObj)
        # Data comes in as 4d numpy array in t,z,y,x order. Swapping axes and       removing the time dimension to give back a 3d numpy array in x,y,z order 
        data = swapaxes(data[0,:,:,:],0,2)

        return data
      
      
      print urlList
      rdd = self.sc.parallelize (enumerate(urlList), len(urlList)).map(lambda (k, v): (k, read(v)))
      return Images(rdd, nimages=len(urlList) )


    def fromTif(self, dataPath, ext='tif', startIdx=None, stopIdx=None, recursive=False):
        """Sets up a new Images object with data to be read from one or more tif files.

        The RDD underlying the returned Images will have key, value data as follows:

        key: int
            key is index of original data file, determined by lexicographic ordering of filenames
        value: numpy ndarray
            value dimensions with be x by y by num_channels*num_pages; all channels and pages in a file are
            concatenated together in the third dimension of the resulting ndarray. For pages 0, 1, etc
            of a multipage TIF of RGB images, ary[:,:,0] will be R channel of page 0 ("R0"), ary[:,:,1] will be B0,
            ... ary[:,:,3] == R1, and so on.

        This method attempts to explicitly import PIL. ImportError may be thrown if 'from PIL import Image' is
        unsuccessful. (PIL/pillow is not an explicit requirement for thunder.)
        """
        try:
            from PIL import Image
        except ImportError, e:
            Image = None
            raise ImportError("fromMultipageTif requires a successful 'from PIL import Image'; " +
                              "the PIL/pillow library appears to be missing or broken.", e)
        # we know that that array(pilimg) works correctly for pillow == 2.3.0, and that it
        # does not work (at least not with spark) for old PIL == 1.1.7. we believe but have not confirmed
        # that array(pilimg) works correctly for every version of pillow. thus currently we check only whether
        # our PIL library is in fact pillow, and choose our conversion function accordingly
        isPillow = hasattr(Image, "PILLOW_VERSION")
        if isPillow:
            conversionFcn = array  # use numpy's array() function
        else:
            from thunder.utils.common import pil_to_array
            conversionFcn = pil_to_array  # use our modified version of matplotlib's pil_to_array

        def multitifReader(buf):
            fbuf = BytesIO(buf)
            multipage = Image.open(fbuf)
            pageIdx = 0
            imgArys = []
            while True:
                try:
                    multipage.seek(pageIdx)
                    imgArys.append(conversionFcn(multipage))
                    pageIdx += 1
                except EOFError:
                    # past last page in tif
                    break
            if len(imgArys) == 1:
                return imgArys[0]
            else:
                return dstack(imgArys)

        reader = getParallelReaderForPath(dataPath)(self.sc)
        readerRdd = reader.read(dataPath, ext=ext, startIdx=startIdx, stopIdx=stopIdx, recursive=recursive)
        return Images(readerRdd.mapValues(multitifReader), nimages=reader.lastNRecs)

    def fromPng(self, dataPath, ext='png', startIdx=None, stopIdx=None, recursive=False):
        """Load an Images object stored in a directory of png files

        The RDD wrapped by the returned Images object will have a number of partitions equal to the number of image data
        files read in by this method.

        Parameters
        ----------

        dataPath: string
            Path to data files or directory, specified as either a local filesystem path or in a URI-like format,
            including scheme. A datapath argument may include a single '*' wildcard character in the filename.

        ext: string, optional, default "png"
            Extension required on data files to be loaded.

        startIdx, stopIdx: nonnegative int. optional.
            Indices of the first and last-plus-one data file to load, relative to the sorted filenames matching
            `datapath` and `ext`. Interpreted according to python slice indexing conventions.

        recursive: boolean, default False
            If true, will recursively descend directories rooted at datapath, loading all files in the tree that
            have an extension matching 'ext'. Recursive loading is currently only implemented for local filesystems
            (not s3).
        """
        def readPngFromBuf(buf):
            fbuf = BytesIO(buf)
            return imread(fbuf, format='png')

        reader = getParallelReaderForPath(dataPath)(self.sc)
        readerRdd = reader.read(dataPath, ext=ext, startIdx=startIdx, stopIdx=stopIdx, recursive=recursive)
        return Images(readerRdd.mapValues(readPngFromBuf), nimages=reader.lastNRecs)
