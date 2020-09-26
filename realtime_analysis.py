import time
import functools
import itertools
import contextlib
import numpy
import scipy
import scipy.fftpack
import scipy.signal
import pyaudio
import wave
import audioread


class DataNode:
    # def send(self, value=None)
    # def __enter__(self)
    # def __exit__(self, type=None, value=None, traceback=None)

    @staticmethod
    def from_generator(gen):
        @functools.wraps(gen)
        def node_builder(*args, **kwargs):
            return GeneratorDataNode(gen(*args, **kwargs))
        return node_builder

    @staticmethod
    def wrap(node_like):
        if isinstance(node_like, DataNode):
            return node_like

        elif hasattr(node_like, "__iter__"):
            @DataNode.from_generator
            @functools.wraps(node_like)
            def iterator():
                yield
                for item in node_like:
                    yield item
            return iterator()

        else:
            @DataNode.from_generator
            @functools.wraps(node_like)
            def pure_func():
                data = yield
                while True:
                    data = yield node_like(data)
            return pure_func()

class GeneratorDataNode(DataNode):
    def __init__(self, generator):
        self.generator = generator
        self.started = False
        self.stopped = False

    def send(self, value=None):
        if not self.started:
            raise RuntimeError("try to access un-initialized data node")
        if self.stopped:
            raise RuntimeError("try to access finalized data node")

        return self.generator.send(value)

    def __enter__(self):
        if self.started:
            return self
        self.started = True

        try:
            next(self.generator)
            return self

        except StopIteration:
            raise RuntimeError("generator didn't yield") from None

    def __exit__(self, type=None, value=None, traceback=None):
        if not self.started:
            raise RuntimeError("try to finalize un-initialized data node")
        if self.stopped:
            return False
        self.stopped = True

        if type is None:
            self.generator.close()
            return False

        try:
            if value is None:
                value = type()
            self.generator.throw(type, value, traceback)

        except BaseException as exc:
            if exc is value:
                return False
            if isinstance(exc, StopIteration):
                return True
            raise

        else:
            raise RuntimeError("generator didn't stop after throw()")

# basic data nodes
@DataNode.from_generator
def delay(prepend):
    """A data node delays signal and prepends given values.

    Parameters
    ----------
    prepend : list or int
        The list of prepended values or number of delay with prepending `None`.

    Receives
    --------
    data : any
        The input signal.

    Yields
    ------
    data : any
        The delayed signal.
    """
    buffer = [None]*prepend if isinstance(prepend, int) else list(prepend)
    data = yield
    while True:
        buffer.append(data)
        data = yield buffer.pop(0)

@DataNode.from_generator
def take(number):
    """A data node takes finite signals.

    Parameters
    ----------
    number : int
        The number of period to take.

    Receives
    --------
    data : any
        The input signal.

    Yields
    ------
    data : any
        The output signal.
    """
    data = yield
    for _ in range(number):
        data = yield data

@DataNode.from_generator
def pipe(*nodes):
    """A data node processes data sequentially.

    Parameters
    ----------
    nodes : list of DataNode
        The data nodes to pipe.

    Receives
    --------
    data : any
        The input signal.

    Yields
    ------
    data : any
        The processed signal.
    """
    nodes = [DataNode.wrap(node) for node in nodes]
    with contextlib.ExitStack() as stack:
        for node in nodes:
            stack.enter_context(node)

        data = yield
        while True:
            for node in nodes:
                data = node.send(data)
            data = yield data

@DataNode.from_generator
def pair(*nodes):
    """A data node processes data parallelly.

    Parameters
    ----------
    nodes : list of DataNode
        The data nodes to pair.

    Receives
    --------
    data : tuple
        The input signal; its length should equal to number of nodes.

    Yields
    ------
    data : tuple
        The processed signal; its length should equal to number of nodes.
    """
    nodes = [DataNode.wrap(node) for node in nodes]
    with contextlib.ExitStack() as stack:
        for node in nodes:
            stack.enter_context(node)

        data = yield
        while True:
            data = yield tuple(node.send(subdata) for node, subdata in zip(nodes, data))

@DataNode.from_generator
def chain(*nodes):
    """A data node processes data with chaining nodes.

    Parameters
    ----------
    nodes : list of DataNode
        The data nodes to chain.

    Receives
    --------
    data : any
        The input signal.

    Yields
    ------
    data : any
        The processed signal.
    """
    nodes = [DataNode.wrap(node) for node in nodes]
    with contextlib.ExitStack() as stack:
        for node in nodes:
            stack.enter_context(node)

        data = yield
        for node in nodes:
            with contextlib.suppress(StopIteration):
                while True:
                    data = yield node.send(data)

@DataNode.from_generator
def nslice(node, start=None, stop=None):
    """A data node processes data with slicing periods.

    Parameters
    ----------
    node : DataNode
        The data node to slice.
    start : int, optional
        The start period to process, default is no slicing.
    end : int, optional
        The end period to process, default is no slicing.

    Receives
    --------
    data : any
        The input signal.

    Yields
    ------
    data : any
        The processed signal.
    """
    node = DataNode.wrap(node)
    index = 0
    with node:
        try:
            while start is not None and start > index:
                index += 1
                node.send()
        except StopIteration:
            yield
            return

        data = yield

        while stop is None or stop > index:
            index += 1
            data = yield node.send(data)

@DataNode.from_generator
def branch(*nodes):
    """A data node processes data additionally.

    Parameters
    ----------
    nodes : list of DataNode
        The sequence of data nodes to branch.

    Receives
    --------
    data : any
        The input signal.

    Yields
    ------
    data : any
        The input signal.
    """
    node = pipe(*nodes)
    with node:
        data = yield
        while True:
            node.send(data)
            data = yield data

@DataNode.from_generator
def merge(*nodes):
    """A data node processes additional data.

    Parameters
    ----------
    nodes : list of DataNode
        The sequence of data nodes to merge.

    Receives
    --------
    data : any
        The input signal.

    Yields
    ------
    data : tuple
        The input signal and additional data.
    """
    node = pipe(*nodes)
    with node:
        data = yield
        while True:
            data = yield (data, node.send())


@DataNode.from_generator
def drip(signals, schedule):
    """A data node to fetch scheduled signals chronologically.

    Parameters
    ----------
    signals : list
        The signals to fetch.

    schedule : function
        A function to schedule each signal, which should return start/end time in a tuple.

    Receives
    --------
    time : float
        The current time to fetch signals, which should greater than previous received time.

    Yields
    ------
    data : list
        The signals occurred in the given time.
    """
    it = iter(sorted((schedule(data), data) for data in signals))

    buffer = []
    waiting = next(it, None)

    time = yield
    while True:
        while waiting is not None and waiting[0][0] < time:
            buffer.append(waiting)
            waiting = next(it, None)

        buffer = [playing for playing in buffer if playing[0][1] >= time]

        time = yield [data for _, data in buffer]

@DataNode.from_generator
def pick_peak(pre_max, post_max, pre_avg, post_avg, wait, delta):
    """A data node of peak detaction.

    Parameters
    ----------
    pre_max : int
    post_max : int
    pre_avg : int
    post_avg : int
    wait : int
    delta : float

    Receives
    --------
    y : float
        The input signal.

    Yields
    ------
    detected : bool
        Whether the signal reaches its peak.
    """
    center = max(pre_max, pre_avg)
    delay = max(post_max, post_avg)
    buffer = numpy.zeros(center+delay+1, dtype=numpy.float32)
    max_buffer = buffer[center-pre_max:center+post_max+1]
    avg_buffer = buffer[center-pre_avg:center+post_avg+1]
    index = -delay
    prev_index = -wait

    buffer[-1] = yield
    while True:
        index += 1
        strength = buffer[center]
        detected = True
        detected = detected and index > prev_index + wait
        detected = detected and strength == max_buffer.max()
        detected = detected and strength >= avg_buffer.mean() + delta

        if detected:
            prev_index = index
        buffer[:-1] = buffer[1:]
        buffer[-1] = yield detected


# for fixed-width data
@DataNode.from_generator
def frame(win_length, hop_length):
    """A data node to frame signal, prepend by zero.

    Parameters
    ----------
    win_length : int
        The length of framed data.
    hop_length : int
        The length of input data.

    Receives
    --------
    data : ndarray
        The input signal.

    Yields
    ------
    data : ndarray
        The framed signal.
    """
    if win_length < hop_length:
        data = yield
        while True:
            data = yield numpy.copy(data[-win_length:])
        return

    data_last = yield
    data = numpy.zeros((win_length, *data_last.shape[1:]), dtype=numpy.float32)
    data[-hop_length:] = data_last

    while True:
        data_last = yield numpy.copy(data)
        data[:-hop_length] = data[hop_length:]
        data[-hop_length:] = data_last

@DataNode.from_generator
def power_spectrum(win_length, samplerate=44100, windowing=True, weighting=True):
    """A data node maps signal `x` to power spectrum `J`.

    Without windowing and weighting, they should satisfy

        (J * df).sum(axis=0) == (x**2).mean(axis=0)

    where the time resolution `dt = 1/samplerate` and the frequency resolution `df = samplerate/win_length`.

    Parameters
    ----------
    win_length : int
        The length of input signal.
    samplerate : int, optional
        The sample rate of input signal, default is `44100`.
    windowing : bool or ndarray, optional
        The window function of signal, `True` for default Hann window, `False` for no windowing.
    weighting : bool or ndarray, optional
        The weight function of spectrum, `True` for default A-weighting, `False` for no weighting.

    Receives
    --------
    x : ndarray
        The input signal.

    Yields
    ------
    J : ndarray
        The power spectrum, with length `win_length//2+1`.
    """
    if isinstance(windowing, bool):
        windowing = get_Hann_window(win_length) if windowing else 1
    if isinstance(weighting, bool):
        weighting = get_A_weight(samplerate, win_length) if weighting else 1
    weighting *= 2/win_length/samplerate

    x = yield
    windowing = windowing.reshape(-1, *[1]*(x.ndim-1)) if numpy.ndim(windowing) > 0 else windowing
    weighting = weighting.reshape(-1, *[1]*(x.ndim-1)) if numpy.ndim(weighting) > 0 else weighting

    while True:
        x = yield weighting * numpy.abs(numpy.fft.rfft(x*windowing, axis=0))**2

@DataNode.from_generator
def onset_strength(df):
    """A data node maps spectrum `J` to onset strength `st`.

    Parameters
    ----------
    df : float
        The frequency resolution of input spectrum.

    Receives
    --------
    J : ndarray
        Input spectrum.

    Yields
    ------
    st : float
        The onset strength between previous and current input spectrum.
    """
    curr = yield
    prev = numpy.zeros_like(curr)
    while True:
        prev, curr = curr, (yield numpy.mean(numpy.maximum(0.0, curr - prev).sum(axis=0)) * df)

@DataNode.from_generator
def draw_spectrum(length, win_length, samplerate=44100, decay=1.0):
    """A data node to show given spectrum by braille patterns.

    Parameters
    ----------
    length : int
        The length of string.
    win_length : int
        The length of input signal before fourier transform.
    samplerate : int, optional
        The sample rate of input signal, default is `44100`.
    decay : float, optional
        The decay volume per period, default is `1.0`.

    Receives
    --------
    J : ndarray
        The power spectrum to draw.

    Yields
    ------
    spec : str
        The string representation of spectrum.
    """
    A = numpy.cumsum([0, 2**6, 2**2, 2**1, 2**0])
    B = numpy.cumsum([0, 2**7, 2**5, 2**4, 2**3])

    df = samplerate/win_length
    n_fft = win_length//2+1
    n = numpy.linspace(1, 88, length*2+1)
    f = 440 * 2**((n-49)/12)
    sec = numpy.minimum(n_fft-1, (f/df).round().astype(int))
    slices = list(zip(sec[:-1], (sec+1)[1:]))

    buf = [0.0]*(length*2)
    J = yield
    while True:
        vols = [power2db(numpy.mean(J[start:end].sum(axis=0)) * df * n_fft/(end-start)) / 60.0 * 4.0 for start, end in slices]
        # buf = [min(4.0, v) for v, prev in zip(vols, buf)]
        buf = [max(0.0, prev-decay, min(4.0, v)) for v, prev in zip(vols, buf)]
        J = yield "".join(chr(0x2800 + A[int(a)] + B[int(b)]) for a, b in zip(buf[0::2], buf[1::2]))

@DataNode.from_generator
def attach(scheduled_signals, samplerate=44100, buffer_shape=1024):
    """A data node attaches scheduled signals to input signal (in place).

    Parameters
    ----------
    scheduled_signals : list
        The list of scheduled signals, composed by tuples of scheduled time and data.
    samplerate : int, optional
        The sample rate to load, default is `44100`.
    buffer_shape : int or tuple, optional
        The shape of input signal, default is `1024`.

    Receives
    --------
    data : ndarray
        The input signal.

    Yields
    ------
    data : ndarray
        The processed signal.
    """
    buffer_length = buffer_shape[0] if isinstance(buffer_shape, tuple) else buffer_shape

    def schedule(item):
        time, data = item
        index = round(time*samplerate)
        return (index - buffer_length, index + data.shape[0])
    dripping_signals = drip(scheduled_signals, schedule)

    with dripping_signals:
        data = yield
        for index in itertools.count(0, buffer_length):
            for time, signal in dripping_signals.send(index):
                start = int(time*samplerate)
                i = max(start, index)
                j = min(start+signal.shape[0], index+buffer_length)
                data[i-index:j-index] += signal[i-start:j-start]
            data = yield data


# for variable-width data
@DataNode.from_generator
def chunk(node, chunk_shape=1024):
    """A data node produces data by chunking given signal.

    Parameters
    ----------
    node : DataNode
        The data node to chunk.
    chunk_shape : int or tuple, optional
        The shape of chunk, default is `1024`.

    Yields
    ------
    data : ndarray
        The chunked signal with shape `chunk_shape`.
    """
    node = DataNode.wrap(node)
    chunk = numpy.zeros(chunk_shape, dtype=numpy.float32)
    index = 0

    with node:
        yield
        try:
            while True:
                data = node.send()
                while data.shape[0] > 0:
                    length = min(chunk.shape[0] - index, data.shape[0])
                    chunk[index:index+length] = data[:length]
                    index += length
                    data = data[length:]

                    if index == chunk.shape[0]:
                        yield numpy.copy(chunk)
                        index = 0

        except StopIteration:
            if index > 0:
                chunk[index:] = 0.0
                yield numpy.copy(chunk)

@DataNode.from_generator
def unchunk(node, chunk_shape=1024):
    """Make a data node receives data with any length.

    Parameters
    ----------
    node : DataNode
        The data node.
    chunk_shape : int or tuple, optional
        The received shape of given data node, default is `1024`.

    Receives
    ------
    data : ndarray
        The unchunked signal with any length.
    """
    node = DataNode.wrap(node)
    chunk = numpy.zeros(chunk_shape, dtype=numpy.float32)
    index = 0

    with node:
        while True:
            data = yield
            while data.shape[0] > 0:
                length = min(chunk.shape[0] - index, data.shape[0])
                chunk[index:index+length] = data[:length]
                index += length
                data = data[length:]

                if index == chunk.shape[0]:
                    node.send(numpy.copy(chunk))
                    index = 0

@DataNode.from_generator
def resample(node, ratio):
    """A data node with resampled data.

    Parameters
    ----------
    node : DataNode
        The data node to resample.
    ratio : float or tuple
        The resampling factor.

    Yields
    ------
    data : ndarray
        The resampled signal.
    """
    node = DataNode.wrap(node)
    index = 0.0
    up, down = (ratio, 1) if isinstance(ratio, float) else ratio

    with node:
        yield
        while True:
            data = node.send()
            next_index = index + data.shape[0] * up/down
            length = int(next_index) - int(index)
            data_ = scipy.signal.resample(data, length, axis=0)
            index = next_index % 1.0
            yield data_

@DataNode.from_generator
def tslice(node, samplerate=44100, start=None, end=None):
    """A data node in given timespan.

    Parameters
    ----------
    node : DataNode
        The data node to slice.
    samplerate : int, optional
        The sample rate of data, default is `44100`.
    start : float, optional
        The start time, default is no slicing.
    end : float, optional
        The end time, default is no slicing.

    Yields
    ------
    data : ndarray
        The signal.
    """
    node = DataNode.wrap(node)
    index = 0
    start = round(start*samplerate) if start is not None else start
    end = round(end*samplerate) if end is not None else end

    with node:
        if start is None or start <= 0:
            yield
            data = node.send()

        else:
            try:
                data = node.send()

                while start >= index + data.shape[0]:
                    index += data.shape[0]
                    data = node.send()
                else:
                    data = data[start-index:]
                    index = start

            except StopIteration:
                yield
                return

        if end is None:
            while True:
                yield data
                data = node.send()

        else:
            while end >= index + data.shape[0]:
                yield data
                index += data.shape[0]
                data = node.send()
            else:
                yield data[:end-index]


# terminal data nodes
@DataNode.from_generator
def load(filename):
    """A data node to load sound file.

    Parameters
    ----------
    filename : str
        The sound file to load.

    Yields
    ------
    data : ndarray
        The loaded signal.
    """
    width = 2
    scale = 2.0 ** (1 - 8*width)
    fmt = "<i{:d}".format(width)

    with audioread.audio_open(filename) as file:
        node = DataNode.wrap(scale * numpy.frombuffer(data, fmt).astype(numpy.float32).reshape(-1, file.channels) for data in file)

        with node:
            yield
            while True:
                yield node.send()

@DataNode.from_generator
def save(filename, samplerate=44100, channels=1, width=2):
    """A data node to save as .wav file.

    Parameters
    ----------
    filename : str
        The sound file to save.
    samplerate : int, optional
        The sample rate, default is `44100`.
    channels : int, optional
        The number of channels, default is `1`.
    width : int, optional
        The sample width in bytes.

    Receives
    ------
    data : ndarray
        The signal to save.
    """
    scale = 2.0 ** (8*width - 1)
    fmt = "<i{:d}".format(width)

    with wave.open(filename, "wb") as file:
        file.setsampwidth(width)
        file.setnchannels(channels)
        file.setframerate(samplerate)
        file.setnframes(0)

        while True:
            file.writeframes(((yield) * scale).astype(fmt).tobytes())


# data node consumers
@contextlib.contextmanager
def record(manager, node, samplerate=44100, buffer_shape=1024, format="f4", device=-1):
    """A context manager of input stream processing by given node.

    Parameters
    ----------
    manager : pyaudio.PyAudio
        The PyAudio object.
    node : DataNode
        The data node to process recorded sound.
    samplerate : int, optional
        The sample rate of input signal, default is `44100`.
    buffer_shape : int or tuple, optional
        The shape of input signal, default is `1024`.
    format : str, optional
        The sample format of input signal, default is `"f4"`.
    device : int, optional
        The input device index, and `-1` for default input device.

    Yields
    ------
    input_stream : pyaudio.Stream
        The stopped input stream to record sound.
    """
    node = DataNode.wrap(node)
    pa_format = {"f4": pyaudio.paFloat32,
                 "i4": pyaudio.paInt32,
                 "i2": pyaudio.paInt16,
                 "i1": pyaudio.paInt8,
                 "u1": pyaudio.paUInt8,
                 }[format]

    scale = 2.0 ** (8*int(format[1]) - 1)
    normalize = {"f4": (lambda d: d),
                 "i4": (lambda d: d / scale),
                 "i2": (lambda d: d / scale),
                 "i1": (lambda d: d / scale),
                 "u1": (lambda d: (d - 64) / 64),
                 }[format]

    if device == -1:
        device = None

    buffer_length, channels = (buffer_shape, 1) if isinstance(buffer_shape, int) else buffer_shape

    def input_callback(in_data, frame_count, time_info, status):
        try:
            if node.stopped:
                raise StopIteration
            data = numpy.frombuffer(in_data, dtype=format).reshape(buffer_shape)
            data = normalize(data)
            node.send(data)

            return b'', pyaudio.paContinue
        except StopIteration:
            return b'', pyaudio.paComplete

    input_stream = manager.open(format=pa_format,
                                channels=channels,
                                rate=samplerate,
                                input=True,
                                output=False,
                                input_device_index=device,
                                frames_per_buffer=buffer_length,
                                stream_callback=input_callback,
                                start=False)

    with node:
        try:
            yield input_stream
        finally:
            input_stream.stop_stream()
            input_stream.close()

@contextlib.contextmanager
def play(manager, node, samplerate=44100, buffer_shape=1024, format="f4", device=-1):
    """A context manager of output stream processing by given node.

    Parameters
    ----------
    manager : pyaudio.PyAudio
        The PyAudio object.
    node : DataNode
        The data node to process playing sound.
    samplerate : int, optional
        The sample rate of output signal, default is `44100`.
    buffer_shape : int or tuple, optional
        The length of output signal, default is `1024`.
    format : str, optional
        The sample format of output signal, default is `"f4"`.
    device : int, optional
        The output device index, and `-1` for default output device.

    Yields
    ------
    output_stream : pyaudio.Stream
        The stopped output stream to play sound.
    """
    node = DataNode.wrap(node)
    pa_format = {"f4": pyaudio.paFloat32,
                 "i4": pyaudio.paInt32,
                 "i2": pyaudio.paInt16,
                 "i1": pyaudio.paInt8,
                 "u1": pyaudio.paUInt8,
                 }[format]

    scale = 2.0 ** (8*int(format[1]) - 1)
    normalize = {"f4": (lambda d: d),
                 "i4": (lambda d: d * scale),
                 "i2": (lambda d: d * scale),
                 "i1": (lambda d: d * scale),
                 "u1": (lambda d: d * 64 + 64),
                 }[format]

    if device == -1:
        device = None

    buffer_length, channels = (buffer_shape, 1) if isinstance(buffer_shape, int) else buffer_shape

    def output_callback(in_data, frame_count, time_info, status):
        try:
            if node.stopped:
                raise StopIteration
            data = node.send(None)
            data = normalize(data).astype(format)
            return data.tobytes(), pyaudio.paContinue
        except StopIteration:
            return b'', pyaudio.paComplete

    output_stream = manager.open(format=pa_format,
                                 channels=channels,
                                 rate=samplerate,
                                 input=False,
                                 output=True,
                                 output_device_index=device,
                                 frames_per_buffer=buffer_length,
                                 stream_callback=output_callback,
                                 start=False)

    with node:
        try:
            yield output_stream
        finally:
            output_stream.stop_stream()
            output_stream.close()

def collect(node, collector=numpy.concatenate):
    """Collect all data generated by given node.

    Parameters
    ----------
    node : DataNode
        The data node to collect.
    collector : function, optional
        The function to process collected data, default is `numpy.concatenate`.

    Returns
    ------
    data : ndarray
        The collected data.
    """
    node = DataNode.wrap(node)
    buffer = []
    with node:
        with contextlib.suppress(StopIteration):
            while True:
                buffer.append(node.send())
    return collector(buffer)

def loop(node, dt=0, until=lambda: False):
    """Loop data node with given time interval.

    Parameters
    ----------
    node : DataNode
        The data node to loop.
    dt : float, optional
        The time interval of each period.
    until : function, optional
        The condition to stop looping.
    """
    node = DataNode.wrap(node)
    with node:
        with contextlib.suppress(StopIteration):
            while not until():
                node.send()
                time.sleep(dt)


# not data nodes
def filter(x, distr):
    return numpy.fft.irfft(numpy.fft.rfft(x, axis=0) * distr, axis=0)

def pulse(samplerate=44100, freq=1000.0, decay_time=0.01, amplitude=1.0, length=None):
    if length is None:
        length = decay_time
    t = numpy.linspace(0, length, int(length*samplerate), endpoint=False, dtype=numpy.float32)
    return amplitude * 2**(-t/decay_time) * numpy.sin(2 * numpy.pi * freq * t)

def power2db(power, scale=(1e-5, 1e6)):
    return 10.0 * numpy.log10(numpy.maximum(scale[0], power*scale[1]))

def get_Hann_window(win_length):
    a = numpy.linspace(0, numpy.pi, win_length)
    window = numpy.sin(a)**2
    gain = (3/8)**0.5 # (window**2).mean()**0.5
    return window / gain

def get_half_Hann_window(win_length):
    a = numpy.linspace(0, numpy.pi/2, win_length)
    window = numpy.sin(a)**2
    return window

def get_A_weight(samplerate, win_length):
    f = numpy.arange(win_length//2+1) * (samplerate/win_length)

    f1 = 20.6
    f2 = 107.7
    f3 = 737.9
    f4 = 12194.0
    weight  = (f**4 * f4**2)**2
    weight /= (f**2 + f1**2)**2
    weight /= (f**2 + f2**2)
    weight /= (f**2 + f3**2)
    weight /= (f**2 + f4**2)**2

    # normalize on 1000 Hz
    f0 = 1000.0
    weight0  = (f0**4 * f4**2)**2
    weight0 /= (f0**2 + f1**2)**2
    weight0 /= (f0**2 + f2**2)
    weight0 /= (f0**2 + f3**2)
    weight0 /= (f0**2 + f4**2)**2
    # weight0 == 10**-0.1

    weight /= weight0
    weight[f<10] = 0.0
    weight[f>20000] = 0.0

    return weight

