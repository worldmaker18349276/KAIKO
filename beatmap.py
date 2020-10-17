import os
import enum
import re
import numpy
import realtime_analysis as ra
import audioread


TOLERANCES = (0.02, 0.06, 0.10, 0.14)
#             GREAT GOOD  BAD   FAILED
BEATS_SYMS = ("□", "■", "⬒", "◎", "◴◵◶◷")
#             Soft Loud Incr Roll Spin
WRONG_SYM = "⬚"
PERF_SYMS = ("\b⟪", "\b⟪", "\b⟨", "  ⟩", "  ⟫", "  ⟫")
SPIN_FINISHED_SYM = "☺"
TARGET_SYMS = ("⛶", "🞎", "🞏", "🞐", "🞑", "🞒", "🞓")

BUF_LENGTH = 512
WIN_LENGTH = 512*4
INCR_TOL = 0.1
SPEC_WIDTH = 5
SPEC_DECAY = 0.01
HIT_DECAY = 0.4
HIT_SUSTAIN = 0.1
PREPARE_TIME = 1.0


# scripts
class Event:
    # lifespan, zindex
    # def play(self, mixer, time): pass
    # def draw(self, bar, time): pass
    pass

class Sym(Event):
    zindex = -2

    def __init__(self, time, symbol=None, speed=1.0, sound=None, samplerate=44100):
        self.time = time
        self.symbol = symbol
        self.speed = speed
        self.sound = sound
        self.samplerate = samplerate
        self.played = False

    @property
    def lifespan(self):
        cross_time = 1.0 / abs(0.5 * self.speed)
        return (self.time-cross_time, self.time+cross_time)

    def play(self, mixer, time):
        if self.sound is not None and not self.played:
            self.played = True
            mixer.play(self.sound, samplerate=self.samplerate, delay=self.time-time)

    def draw(self, bar, time):
        if self.symbol is not None:
            pos = (self.time - time) * 0.5 * self.speed
            bar.draw_sym(pos, self.symbol)


# hit objects
class HitObject(Event):
    # lifespan, range, score, total_score, finished
    # def hit(self, time, strength): pass
    # def finish(self): pass
    # def play(self, mixer, time): pass
    # def draw(self, bar, time): pass
    # def draw_judging(self, bar, time): pass
    # def draw_hitting(self, bar, time): pass

    tolerances = TOLERANCES

    @property
    def zindex(self):
        return -1 if self.finished else 1

    def draw_judging(self, bar, time): pass
    def draw_hitting(self, bar, time): pass

class SingleHitObject(HitObject):
    # time, speed, volume, perf, played, symbol, sound, samplerate
    # def hit(self, time, strength): pass

    total_score = 10
    perf_syms = PERF_SYMS
    wrong_symbol = WRONG_SYM

    def __init__(self, time, speed=1.0, volume=0.0):
        self.time = time
        self.speed = speed
        self.volume = volume
        self.perf = None
        self.played = False

    @property
    def range(self):
        return (self.time - self.tolerances[3], self.time + self.tolerances[3])

    @property
    def score(self):
        return self.perf.score if self.perf is not None else 0

    @property
    def finished(self):
        return self.perf is not None

    def finish(self):
        self.perf = Performance.MISS

    def hit(self, time, strength, is_correct_key):
        self.perf = Performance.judge(time - self.time, is_correct_key, self.tolerances)

    @property
    def lifespan(self):
        cross_time = 1.0 / abs(0.5 * self.speed)
        return (self.time-cross_time, self.time+cross_time)

    def play(self, mixer, time):
        if not self.played:
            self.played = True
            sound = [s * 10**(self.volume/20) for s in self.sound]
            mixer.play(sound, samplerate=self.samplerate, delay=self.time-time)

    def draw(self, bar, time):
        CORRECT_TYPES = (Performance.GREAT,
                         Performance.LATE_GOOD, Performance.EARLY_GOOD,
                         Performance.LATE_BAD, Performance.EARLY_BAD,
                         Performance.LATE_FAILED, Performance.EARLY_FAILED)

        if self.perf in (None, Performance.MISS):
            pos = (self.time - time) * 0.5 * self.speed
            bar.draw_sym(pos, self.symbol)

        elif self.perf not in CORRECT_TYPES:
            pos = (self.time - time) * 0.5 * self.speed
            bar.draw_sym(pos, self.wrong_symbol)

    def draw_hitting(self, bar, time):
        self.perf.draw(bar, self.speed < 0, self.perf_syms)

class Performance(enum.Enum):
    MISS               = ("Miss"                      , 0)
    GREAT              = ("Great"                     , 10)
    LATE_GOOD          = ("Late Good"                 , 5)
    EARLY_GOOD         = ("Early Good"                , 5)
    LATE_BAD           = ("Late Bad"                  , 3)
    EARLY_BAD          = ("Early Bad"                 , 3)
    LATE_FAILED        = ("Late Failed"               , 0)
    EARLY_FAILED       = ("Early Failed"              , 0)
    GREAT_WRONG        = ("Great but Wrong Key"       , 5)
    LATE_GOOD_WRONG    = ("Late Good but Wrong Key"   , 3)
    EARLY_GOOD_WRONG   = ("Early Good but Wrong Key"  , 3)
    LATE_BAD_WRONG     = ("Late Bad but Wrong Key"    , 1)
    EARLY_BAD_WRONG    = ("Early Bad but Wrong Key"   , 1)
    LATE_FAILED_WRONG  = ("Late Failed but Wrong Key" , 0)
    EARLY_FAILED_WRONG = ("Early Failed but Wrong Key", 0)

    def __repr__(self):
        return "Performance." + self.name

    def __str__(self):
        return self.value[0]

    @property
    def score(self):
        return self.value[1]

    @staticmethod
    def judge(time_diff, is_correct_key, tolerances):
        err = abs(time_diff)
        too_late = time_diff > 0

        if err < tolerances[0]:
            if is_correct_key:
                perf = Performance.GREAT
            else:
                perf = Performance.GREAT_WRONG

        elif err < tolerances[1]:
            if is_correct_key:
                perf = Performance.LATE_GOOD         if too_late else Performance.EARLY_GOOD
            else:
                perf = Performance.LATE_GOOD_WRONG   if too_late else Performance.EARLY_GOOD_WRONG

        elif err < tolerances[2]:
            if is_correct_key:
                perf = Performance.LATE_BAD          if too_late else Performance.EARLY_BAD
            else:
                perf = Performance.LATE_BAD_WRONG    if too_late else Performance.EARLY_BAD_WRONG

        else:
            if is_correct_key:
                perf = Performance.LATE_FAILED       if too_late else Performance.EARLY_FAILED
            else:
                perf = Performance.LATE_FAILED_WRONG if too_late else Performance.EARLY_FAILED_WRONG

        return perf

    def draw(self, bar, flipped, perf_syms):
        LEFT_GOOD    = (Performance.LATE_GOOD,    Performance.LATE_GOOD_WRONG)
        RIGHT_GOOD   = (Performance.EARLY_GOOD,   Performance.EARLY_GOOD_WRONG)
        LEFT_BAD     = (Performance.LATE_BAD,     Performance.LATE_BAD_WRONG)
        RIGHT_BAD    = (Performance.EARLY_BAD,    Performance.EARLY_BAD_WRONG)
        LEFT_FAILED  = (Performance.LATE_FAILED,  Performance.LATE_FAILED_WRONG)
        RIGHT_FAILED = (Performance.EARLY_FAILED, Performance.EARLY_FAILED_WRONG)
        if flipped:
            LEFT_GOOD, RIGHT_GOOD = RIGHT_GOOD, LEFT_GOOD
            LEFT_BAD, RIGHT_BAD = RIGHT_BAD, LEFT_BAD
            LEFT_FAILED, RIGHT_FAILED = RIGHT_FAILED, LEFT_FAILED

        if self in LEFT_GOOD:
            bar.draw_sym(0.0, perf_syms[2])
        elif self in RIGHT_GOOD:
            bar.draw_sym(0.0, perf_syms[3])
        elif self in LEFT_BAD:
            bar.draw_sym(0.0, perf_syms[1])
        elif self in RIGHT_BAD:
            bar.draw_sym(0.0, perf_syms[4])
        elif self in LEFT_FAILED:
            bar.draw_sym(0.0, perf_syms[0])
        elif self in RIGHT_FAILED:
            bar.draw_sym(0.0, perf_syms[5])

class Soft(SingleHitObject):
    symbol = BEATS_SYMS[0]
    sound = [ra.pulse(samplerate=44100, freq=830.61, decay_time=0.03, amplitude=0.5)]
    samplerate = 44100

    def hit(self, time, strength):
        super().hit(time, strength, strength < 0.5)

class Loud(SingleHitObject):
    symbol = BEATS_SYMS[1]
    sound = [ra.pulse(samplerate=44100, freq=1661.2, decay_time=0.03, amplitude=1.0)]
    samplerate = 44100

    def hit(self, time, strength):
        super().hit(time, strength, strength >= 0.5)

class IncrGroup:
    def __init__(self, threshold=0.0, total=0):
        self.threshold = threshold
        self.total = total

    def add(self, time, speed=1.0, volume=0.0):
        self.total += 1
        return Incr(time, self.total, self, speed=speed, volume=volume)

    def hit(self, strength):
        self.threshold = max(self.threshold, strength)

class Incr(SingleHitObject):
    symbol = BEATS_SYMS[2]
    samplerate = 44100
    incr_tol = INCR_TOL

    def __init__(self, time, count, group, speed=1.0, volume=0.0):
        super().__init__(time, speed, volume)
        self.count = count
        self.group = group

    def hit(self, time, strength):
        super().hit(time, strength, strength >= self.group.threshold - self.incr_tol)
        self.group.hit(strength)

    @property
    def sound(self):
        amplitude = (0.2 + 0.8 * (self.count-1)/self.group.total) * 10**(self.volume/20)
        return [ra.pulse(samplerate=44100, freq=1661.2, decay_time=0.03, amplitude=amplitude)]

class Roll(HitObject):
    symbol = BEATS_SYMS[3]
    sound = [ra.pulse(samplerate=44100, freq=1661.2, decay_time=0.01, amplitude=0.5)]
    samplerate = 44100

    def __init__(self, time, step, number, speed=1.0, volume=0.0):
        self.time = time
        self.step = step
        self.number = number
        self.speed = speed
        self.volume = volume
        self.roll = 0
        self.finished = False
        self.played = False

    @property
    def range(self):
        return (self.time - self.tolerances[2],
                self.time + self.step * self.number - min(self.step, self.tolerances[2]))

    @property
    def total_score(self):
        return self.number * 2

    @property
    def score(self):
        if self.roll < self.number:
            return self.roll * 2
        elif self.roll < 2*self.number:
            return (2*self.number - self.roll) * 2
        else:
            return 0

    def hit(self, time, strength):
        self.roll += 1

    def finish(self):
        self.finished = True

    @property
    def lifespan(self):
        cross_time = 1.0 / abs(0.5 * self.speed)
        return (self.time-cross_time, self.time+self.step*self.number+cross_time)

    def play(self, mixer, time):
        if not self.played:
            self.played = True

            for r in range(self.number):
                delay = self.time + self.step * r - time
                sound = [s * 10**(self.volume/20) for s in self.sound]
                mixer.play(sound, samplerate=self.samplerate, delay=delay)

    def draw(self, bar, time):
        for r in range(self.number):
            if r > self.roll-1:
                pos = (self.time + self.step * r - time) * 0.5 * self.speed
                bar.draw_sym(pos, self.symbol)

class Spin(HitObject):
    total_score = 10
    symbols = BEATS_SYMS[4]
    sound = [ra.pulse(samplerate=44100, freq=1661.2, decay_time=0.01, amplitude=1.0)]
    samplerate = 44100
    finished_sym = SPIN_FINISHED_SYM

    def __init__(self, time, duration, capacity, speed=1.0, volume=0.0):
        self.time = time
        self.duration = duration
        self.capacity = capacity
        self.speed = speed
        self.volume = volume
        self.charge = 0.0
        self.finished = False
        self.played = False

    @property
    def range(self):
        return (self.time - self.tolerances[2], self.time + self.duration + self.tolerances[2])

    @property
    def score(self):
        return self.total_score if self.charge == self.capacity else 0

    def hit(self, time, strength):
        self.charge = min(self.charge + min(1.0, strength), self.capacity)
        if self.charge == self.capacity:
            self.finished = True

    def finish(self):
        self.finished = True

    @property
    def lifespan(self):
        cross_time = 1.0 / abs(0.5 * self.speed)
        return (self.time-cross_time, self.time+self.duration+cross_time)

    def play(self, mixer, time):
        if not self.played:
            self.played = True

            step = self.duration/self.capacity if self.capacity > 0.0 else 0.0
            for i in range(int(self.capacity)):
                delay = self.time + step * i - time
                sound = [s * 10**(self.volume/20) for s in self.sound]
                mixer.play(sound, samplerate=44100, delay=delay)

    def draw(self, bar, time):
        if self.charge < self.capacity:
            pos = 0.0
            pos += max(0.0, (self.time - time) * 0.5 * self.speed)
            pos += min(0.0, (self.time + self.duration - time) * 0.5 * self.speed)
            bar.draw_sym(pos, self.symbols[int(self.charge) % 4])

    def draw_judging(self, bar, time):
        return True

    def draw_hitting(self, bar, time):
        if self.charge == self.capacity:
            bar.draw_sym(0.0, self.finished_sym)
            return True


# beatmap
class ScrollingBar:
    def __init__(self, width, shift, spec_width):
        self.width = width
        self.shift = shift
        self.spec_width = spec_width

        self.chars = [' ']*width
        self.spec_offset = 1
        self.score_offset = self.spec_width + 2
        self.progress_offset = self.width - 9
        self.bar_offset = self.spec_width + 15
        self.bar_width = self.width - 24 - self.spec_width

    def __str__(self):
        return "".join(self.chars)

    def clear(self):
        for i in range(self.width):
            self.chars[i] = ' '

    def addstr(self, index, str):
        for ch in str:
            if ch == ' ':
                index += 1
            elif ch == '\b':
                index -= 1
            else:
                if index in range(self.width):
                    self.chars[index] = ch
                index += 1

    def draw_spectrum(self, spectrum):
        self.addstr(self.spec_offset, spectrum)

    def draw_score(self, score, total_score):
        self.addstr(self.score_offset, "[{:>5d}/{:>5d}]".format(score, total_score))

    def draw_progress(self, progress):
        self.addstr(self.progress_offset, "[{:>5.1f}%]".format(progress*100))

    def draw_sym(self, pos, sym):
        index = round((pos + self.shift) * (self.bar_width - 1))
        for ch in sym:
            if ch == ' ':
                index += 1
            elif ch == '\b':
                index -= 1
            else:
                if index in range(self.bar_width):
                    self.chars[self.bar_offset+index] = ch
                index += 1

class Beatmap:
    prepare_time = PREPARE_TIME
    buffer_length = BUF_LENGTH
    win_length = WIN_LENGTH
    spec_width = SPEC_WIDTH
    spec_decay = SPEC_DECAY

    hit_decay = HIT_DECAY
    hit_sustain = HIT_SUSTAIN
    target_syms = TARGET_SYMS

    def __init__(self, audio, events):
        # audio metadata
        self.audio = audio
        if self.audio is not None:
            with audioread.audio_open(self.audio) as file:
                self.duration = file.duration
                self.samplerate = file.samplerate
                self.channels = file.channels
        else:
            self.duration = 0.0
            self.samplerate = 44100
            self.channels = 1

        # events, hits
        self.events = list(events)
        self.hits = [event for event in self.events if isinstance(event, HitObject)]
        self.start = min([0.0, *[event.lifespan[0] - self.prepare_time for event in self.events]])
        self.end = max([self.duration, *[event.lifespan[1] + self.prepare_time for event in self.events]])

        # hit state
        self.judging_object = None
        self.hit_index = 0
        self.hit_time = self.start - max(self.hit_decay, self.hit_sustain)*2
        self.hit_strength = 0.0
        self.hit_object = None
        self.draw_index = self.hit_index
        self.draw_time = self.hit_time

        # spectrum show
        self.spectrum = " "*self.spec_width

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        return False

    @property
    def total_score(self):
        return sum(hit.total_score for hit in self.hits)

    @property
    def score(self):
        return sum(hit.score for hit in self.hits)

    @property
    def progress(self):
        if len(self.hits) == 0:
            return 1.0
        return sum(1 for hit in self.hits if hit.finished) / len(self.hits)

    @ra.DataNode.from_generator
    def get_hits_handler(self):
        hits = iter(sorted(self.hits, key=lambda e: e.range))
        hit = next(hits, None)

        time = yield
        while True:
            while hit is not None and (hit.finished or time > hit.range[1]):
                if not hit.finished:
                    hit.finish()
                hit = next(hits, None)

            time = yield (hit if hit is not None and time > hit.range[0] else None)

    @ra.DataNode.from_generator
    def get_knock_handler(self):
        hits_handler = self.get_hits_handler()

        with hits_handler:
            while True:
                time, strength, detected = yield
                time += self.start

                self.judging_object = hits_handler.send(time)

                if not detected:
                    continue

                # hit note
                self.hit_index += 1
                self.hit_time = time
                self.hit_strength = min(1.0, strength)
                self.hit_object = self.judging_object

                if self.judging_object is None:
                    continue

                self.judging_object.hit(time, strength)
                self.judging_object = hits_handler.send(time)

    def get_spectrum_handler(self):
        Dt = self.buffer_length / self.samplerate
        spec = ra.pipe(ra.frame(self.win_length, self.buffer_length),
                       ra.power_spectrum(self.win_length, samplerate=self.samplerate),
                       ra.draw_spectrum(self.spec_width, win_length=self.win_length,
                                                         samplerate=self.samplerate,
                                                         decay=Dt/self.spec_decay/4),
                       lambda s: setattr(self, "spectrum", s))
        return spec

    @ra.DataNode.from_generator
    def get_sound_handler(self, mixer):
        # generate sound
        if isinstance(self.audio, str):
            music = ra.load(self.audio)

            # add spec
            music = ra.chunk(music, chunk_shape=(self.buffer_length, self.channels))
            music = ra.pipe(music, ra.branch(self.get_spectrum_handler()))

            mixer.play(music, samplerate=self.samplerate, delay=-self.start)

        elif self.audio is not None:
            raise ValueError

        events_dripper = ra.drip(self.events, lambda e: e.lifespan)

        with events_dripper:
            time = (yield) + self.start
            while time < self.end:
                for event in events_dripper.send(time):
                    event.play(mixer, time)
                time = (yield) + self.start

    def draw_target(self, bar, time):
        strength = self.hit_strength - (time - self.draw_time) / self.hit_decay
        strength = max(0.0, min(1.0, strength))
        loudness = int(strength * (len(self.target_syms) - 1))
        if abs(time - self.hit_time) < self.hit_sustain:
            loudness = max(1, loudness)
        bar.draw_sym(0.0, self.target_syms[loudness])

    @ra.DataNode.from_generator
    def get_view_handler(self):
        bar_shift = 0.1
        width = int(os.popen("stty size", "r").read().split()[1])
        bar = ScrollingBar(width, bar_shift, self.spec_width)

        events_dripper = ra.drip(self.events, lambda e: e.lifespan)

        with events_dripper:
            try:
                while True:
                    time = yield
                    time += self.start

                    if self.draw_index != self.hit_index:
                        self.draw_time = time
                        self.draw_index = self.hit_index

                    # draw events
                    bar.clear()
                    events = events_dripper.send(time)
                    for event in sorted(events[::-1], key=lambda e: e.zindex):
                        event.draw(bar, time)

                    # draw target
                    stop_drawing_target = False
                    if not stop_drawing_target and self.judging_object is not None:
                        stop_drawing_target = self.judging_object.draw_judging(bar, time)
                    if not stop_drawing_target and self.hit_object is not None:
                        if abs(time - self.draw_time) < self.hit_sustain:
                            stop_drawing_target = self.hit_object.draw_hitting(bar, time)
                    if not stop_drawing_target:
                        self.draw_target(bar, time)

                    # draw others
                    bar.draw_spectrum(self.spectrum)
                    bar.draw_score(self.score, self.total_score)
                    bar.draw_progress(self.progress)

                    # render
                    print('\r' + str(bar) + '\r', end='', flush=True)

            finally:
                print()


def make_std_regex():
    exprs = dict(
        number=r"([-+]?(0|[1-9][0-9]*)(\.[0-9]+|/[1-9][0-9]*)?)",
        str=r"('((?![\\\r\n]|').|\\.|\\\r\n)*')",
        mstr=r"('''((?!\\|''').|\\.)*''')",
        nl=r"((\#[^\r\n$]*)?(\r\n?|\n|$))",
        sp=r"[ ]",
        )

    notes = dict(
        rest=r" ",
        soft=r"( | time = {number} | speed = {number} | time = {number} , speed = {number} )",
        loud=r"( | time = {number} | speed = {number} | time = {number} , speed = {number} )",
        incr=r" {str} (, time = {number} )?(, speed = {number} )?",
        roll=r" {number} , {number} (, time = {number} )?(, speed = {number} )?",
        spin=r" {number} , {number} (, time = {number} )?(, speed = {number} )?",
        sym=r" {str} (, time = {number} )?(, speed = {number} )?",
        pattern=r" {number} , {number} , {mstr} ",
        )

    exprs["note"] = "(" + "|".join((name + r" \(" + args + r"\)").replace(" ", "{sp}*")
                                 for name, args in notes.items()).format(**exprs) + ")"

    header = r"#K-AIKO-std-(?P<version>\d+\.\d+\.\d+)(\r\n?|\n)"

    main = r"""
    ({sp}*{nl})*
    (sheet \. metadata = {mstr} ({sp}*{nl})+)?
    (sheet \. audio = {str} ({sp}*{nl})+)?
    (sheet \. offset = {number} ({sp}*{nl})+)?
    (sheet \. tempo = {number} ({sp}*{nl})+)?
    (sheet \[ {str} \] = {note} ({sp}*{nl})+)*
    (sheet \+= {note} ({sp}*{nl})+)*
    """
    main = main.replace("\n    ", "").replace(" ", "{sp}*").format(**exprs)

    return re.compile(header + main, re.S)

class BeatSheetStd:
    version = "0.0.1"
    regex = make_std_regex()

    def __init__(self):
        self.metadata = ""
        self.audio = None
        self.offset = 0.0
        self.tempo = 60.0

        self.incr_groups = dict()
        self.patterns = dict()
        self.events = []

    def time(self, t, offset=None):
        if offset is None:
            offset = self.offset
        return offset+t*60.0/self.tempo

    def rest(self):
        return lambda t: []

    def soft(self, time=0, speed=1.0, volume=0.0):
        return lambda t: [Soft(self.time(time+t), speed=speed, volume=volume)]

    def loud(self, time=0, speed=1.0, volume=0.0):
        return lambda t: [Loud(self.time(time+t), speed=speed, volume=volume)]

    def incr(self, group, time=0, speed=1.0, volume=0.0):
        if group not in self.incr_groups:
            self.incr_groups[group] = IncrGroup()
        return lambda t: [self.incr_groups[group].add(self.time(time+t), speed=speed, volume=volume)]

    def roll(self, step, number, time=0, speed=1.0, volume=0.0):
        return lambda t: [Roll(self.time(time+t), self.time(step, 0), number, speed=speed, volume=volume)]

    def spin(self, duration, density, time=0, speed=1.0, volume=0.0):
        return lambda t: [Spin(self.time(time+t), self.time(duration, 0), duration*density, speed=speed, volume=volume)]

    def sym(self, symbol, time=0, speed=1.0):
        return lambda t: [Sym(self.time(time+t), symbol=symbol, speed=speed)]

    def pattern(self, time, step, term):
        return lambda t: [note for i, p in enumerate(term.split()) for note in self.patterns[p](time+t+i*step)]

    def __setitem__(self, key, value):
        if not isinstance(key, str) or re.search(r"\s", key):
            raise KeyError("invalid key: {!r}".format(key))
        self.patterns[key] = value

    def __iadd__(self, value):
        self.events += value(0)
        return self

    def load(self, str):
        match = self.regex.fullmatch(str)
        if not match:
            raise ValueError("invalid syntax")
        if match.group("version") != self.version:
            raise ValueError("wrong version: {}".format(match.group("version")))

        terms = {
            "sheet": self,
            "rest": self.rest,
            "soft": self.soft,
            "loud": self.loud,
            "incr": self.incr,
            "roll": self.roll,
            "spin": self.spin,
            "sym": self.sym,
            "pattern": self.pattern
            }
        exec(str, dict(), terms)

    def load_from_osu(self, str):
        regex = r"""osu file format v(?P<version>\d+)

        \[General\]
        ...
        AudioFilename:[ ]*(?P<audio>.*?)[ ]*
        ...

        \[Editor\]
        ...

        \[Metadata\]
        (?P<metadata>(.|\n)*?)

        \[Difficulty\]
        ...
        SliderMultiplier:[ ]*(?P<multiplier>\d+(.\d+)?)[ ]*
        ...

        \[Events\]
        ...

        \[TimingPoints\]
        (?P<timings>(.|\n)*?)

        \[HitObjects\]
        (?P<notes>(.|\n)*?)[\n\r]*"""

        regex = regex.replace("\n        ", "\n")
        regex = regex.replace("\n\n", "\n" + r"[\n\r]*")
        regex = regex.replace("...\n", r"(.*\n)*?")
        regex = re.compile(regex)

        match = regex.fullmatch(str.replace("\r\n", "\n"))
        if not match:
            raise ValueError("invalid syntax")
        if match.group("version") != "14":
            raise ValueError("wrong version: {}".format(match.group("version")))

        self.audio = match.group("audio")
        self.metadata = "\n" + match.group("metadata") + "\n"
        self.offset = 0.0
        self.tempo = 60.0

        multiplier = multiplier0 = float(match.group("multiplier"))
        timings = match.group("timings").split()
        notes = match.group("notes").split()
        note_length = 0
        meter = 4

        @ra.DataNode.from_generator
        def timer():
            nonlocal meter, note_length, multiplier, multiplier0
            format = re.compile(r"(?P<time>\d+),(?P<length>[-+.\d]+),(?P<meter>\d+),"
                                r"\d+,\d+,\d+,(?P<uninherited>0|1),\d+")

            time = yield
            for timing in timings:
                match = format.fullmatch(timing)
                if not match:
                    raise ValueError("wrong timing point format: {}".format(timing))

                while time < int(match.group("time")):
                    time = yield


                if match.group("uninherited") == "1":
                    note_length = float(match.group("length"))
                    meter = int(match.group("meter"))
                else:
                    multiplier = multiplier0 / (-0.01 * float(match.group("length")))
        timer = timer()

        with timer:
            for note in notes:
                note = note.split(",")
                time = int(note[2])
                type = int(note[3])
                try:
                    timer.send(time)
                except StopIteration:
                    pass
                speed = multiplier / 1.4

                # type: [_:_:_:_:Spinner:_:Slider:Circle]
                # hit_sound: [Loud:Big:Loud:Soft]

                if type & 1: # circle
                    hit_sound = int(note[4])

                    if hit_sound == 0 or hit_sound & 1:
                        self += self.soft(time=time/1000, speed=speed)
                    elif hit_sound & 10:
                        self += self.loud(time=time/1000, speed=speed)

                elif type & 2: # slider
                    slider_length = int(note[7])
                    duration = slider_length / (multiplier * 100) * (note_length/1000)
                    step = (note_length/1000) * meter / 8

                    self += self.roll(duration, step, time=time/1000, speed=speed)

                elif type & 8: # spinner
                    end_time = int(note[5])
                    duration = (end_time - time)/1000
                    step = (note_length/1000) * meter / 8

                    self += self.spin(duration, step, time=time/1000, speed=speed)

