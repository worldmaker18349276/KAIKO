import sys
import os
import contextlib
import traceback
import zipfile
import shutil
import psutil
from pathlib import Path
import appdirs
import pyaudio
from . import datanodes as dn
from . import cfg
from . import tui
from . import kerminal
from . import beatcmd
from .beatmap import BeatmapPlayer
from .beatsheet import BeatmapDraft, BeatmapParseError
from . import beatanalyzer


logo = """

  ██▀ ▄██▀   ▄██████▄ ▀██████▄ ██  ▄██▀ █▀▀▀▀▀▀█
  ▀ ▄██▀  ▄▄▄▀█    ██    ██    ██▄██▀   █ ▓▓▓▓ █
  ▄██▀██▄ ▀▀▀▄███████    ██    ███▀██▄  █ ▓▓▓▓ █
  █▀   ▀██▄  ██    ██ ▀██████▄ ██   ▀██▄█▄▄▄▄▄▄█


  🎧  Use headphones for the best experience 🎝 

"""

def print_pyaudio_info(manager):
    print()

    print("portaudio version:")
    print("  " + pyaudio.get_portaudio_version_text())
    print()

    print("available devices:")
    apis_list = [manager.get_host_api_info_by_index(i)['name'] for i in range(manager.get_host_api_count())]

    table = []
    for index in range(manager.get_device_count()):
        info = manager.get_device_info_by_index(index)

        ind = str(index)
        name = info['name']
        api = apis_list[info['hostApi']]
        freq = str(info['defaultSampleRate']/1000)
        chin = str(info['maxInputChannels'])
        chout = str(info['maxOutputChannels'])

        table.append((ind, name, api, freq, chin, chout))

    ind_len   = max(len(entry[0]) for entry in table)
    name_len  = max(len(entry[1]) for entry in table)
    api_len   = max(len(entry[2]) for entry in table)
    freq_len  = max(len(entry[3]) for entry in table)
    chin_len  = max(len(entry[4]) for entry in table)
    chout_len = max(len(entry[5]) for entry in table)

    for ind, name, api, freq, chin, chout in table:
        print(f"  {ind:>{ind_len}}. {name:{name_len}}  by  {api:{api_len}}"
              f"  ({freq:>{freq_len}} kHz, in: {chin:>{chin_len}}, out: {chout:>{chout_len}})")

    print()

    default_input_device_index = manager.get_default_input_device_info()['index']
    default_output_device_index = manager.get_default_output_device_info()['index']
    print(f"default input device: {default_input_device_index}")
    print(f"default output device: {default_output_device_index}")

class KAIKOTheme(metaclass=cfg.Configurable):
    data_icon: str = "\x1b[92m🗀 \x1b[m"
    info_icon: str = "\x1b[94m🛠 \x1b[m"
    hint_icon: str = "\x1b[93m💡 \x1b[m"

    verb_attr: str = "2"
    emph_attr: str = "1"
    warn_attr: str = "31"

@beatcmd.promptable
class KAIKOGame:
    def __init__(self, theme, data_dir, songs_dir, manager):
        self.theme = theme
        self._data_dir = data_dir
        self._songs_dir = songs_dir
        self.manager = manager
        self._beatmaps = []
        self.songs_mtime = None

    @classmethod
    @contextlib.contextmanager
    def init(clz, theme_path=None):
        # print logo
        print(logo, flush=True)

        # load theme
        theme = KAIKOTheme()
        if theme_path is not None:
            cfg.config_read(open(theme_path, 'r'), main=theme)

        data_icon = theme.data_icon
        info_icon = theme.info_icon
        verb_attr = theme.verb_attr
        emph_attr = theme.emph_attr

        # load user data
        data_dir = Path(appdirs.user_data_dir("K-AIKO", psutil.Process().username()))
        songs_dir = data_dir / "songs"

        if not data_dir.exists():
            # start up
            print(f"{data_icon} preparing your profile...")
            data_dir.mkdir(exist_ok=True)
            songs_dir.mkdir(exist_ok=True)
            print(f"{data_icon} your data will be stored in "
                  f"{tui.add_attr(data_dir.as_uri(), emph_attr)}")
            print(flush=True)

        # load PyAudio
        print(f"{info_icon} Loading PyAudio...")
        print()

        print(f"\x1b[{verb_attr}m", end="", flush=True)
        try:
            manager = pyaudio.PyAudio()
            print_pyaudio_info(manager)
        finally:
            print("\x1b[m", flush=True)

        try:
            yield clz(theme, data_dir, songs_dir, manager)
        finally:
            manager.terminate()

    @beatcmd.promptable
    def data_dir(self):
        return self._data_dir

    @beatcmd.promptable
    def songs_dir(self):
        return self._songs_dir

    @beatcmd.promptable
    def beatmaps(self):
        if self.songs_mtime != os.stat(str(self._songs_dir)).st_mtime:
            self.reload()

        return self._beatmaps

    @beatcmd.promptable
    def reload(self):
        info_icon = self.theme.info_icon
        emph_attr = self.theme.emph_attr
        songs_dir = self._songs_dir

        print(f"{info_icon} Loading songs from {tui.add_attr(songs_dir.as_uri(), emph_attr)}...")

        self._beatmaps = []

        for file in songs_dir.iterdir():
            if file.is_dir() and file.suffix == ".osz":
                distpath = file.parent / file.stem
                if distpath.exists():
                    continue
                print(f"{info_icon} Unzip file {tui.add_attr(filepath.as_uri(), emph_attr)}...")
                distpath.mkdir()
                zf = zipfile.ZipFile(str(filepath), 'r')
                zf.extractall(path=str(distpath))

        # for root, dirs, files in os.walk(songs_dir): for file in files:
        for song in songs_dir.iterdir():
            if song.is_dir():
                for beatmap in song.iterdir():
                    if beatmap.suffix in (".kaiko", ".ka", ".osu"):
                        self._beatmaps.append(beatmap.relative_to(str(songs_dir)))

        if len(self._beatmaps) == 0:
            print("{info_icon} There is no song in the folder yet!")
        print(flush=True)

        self.songs_mtime = os.stat(str(songs_dir)).st_mtime

    @beatcmd.promptable
    def add(self, beatmap:Path):
        info_icon = self.theme.info_icon
        emph_attr = self.theme.emph_attr
        warn_attr = self.theme.warn_attr
        songs_dir = self._songs_dir

        if not beatmap.exists():
            print(tui.add_attr(f"File not found: {str(beatmap)}", warn_attr))
            return

        print(f"{info_icon} Adding new song from {tui.add_attr(beatmap.as_uri(), emph_attr)}...")

        if beatmap.is_file():
            shutil.copy(str(beatmap), str(songs_dir))
        elif beatmap.is_dir():
            shutil.copytree(str(beatmap), str(songs_dir))
        else:
            print(tui.add_attr(f"Not a file: {str(beatmap)}", warn_attr))
            return

        self.reload()

    @beatcmd.promptable
    def play(self, beatmap:Path):
        if not beatmap.is_absolute():
            beatmap = self._songs_dir.joinpath(beatmap)
        return KAIKOPlay(beatmap)

    @beatcmd.promptable
    def say(self, message:str, escape:bool=False):
        if escape:
            print(beatcmd.echo_str(message))
        else:
            print(message)

    @beatcmd.promptable
    def exit(self):
        print("bye~")
        raise KeyboardInterrupt

class KAIKOPlay:
    def __init__(self, filepath):
        self.filepath = filepath

    @contextlib.contextmanager
    def execute(self, manager):
        try:
            beatmap = BeatmapDraft.read(str(self.filepath))

        except BeatmapParseError:
            print(f"failed to read beatmap {str(self.filepath)}")

        else:
            game = BeatmapPlayer(beatmap)
            game.execute(manager)

            print()
            beatanalyzer.show_analyze(beatmap.settings.performance_tolerance, game.perfs)


def main():
    try:
        with KAIKOGame.init() as game:
            # load songs
            game.reload()

            # play given beatmap
            if len(sys.argv) > 1:
                res = beatcmd.Promptable(game).generate(sys.argv[1:])()
                if hasattr(res, 'execute'):
                    res.execute(game.manager)
                return

            # prompt
            history = []
            while True:
                result = beatcmd.prompt(game, history)

                if hasattr(result, 'execute'):
                    result.execute(game.manager)

                elif isinstance(result, list):
                    for item in result:
                        print(item)

                elif result is not None:
                    print(result)

    except KeyboardInterrupt:
        pass

    except:
        # print error
        print("\x1b[31m", end="")
        traceback.print_exc(file=sys.stdout)
        print(f"\x1b[m", end="")

if __name__ == '__main__':
    main()
