"""Resolve only the CMake preset fields needed to verify lock resources.

CMake still owns configuration and build options. No CMake command is run to
discover paths: even a build-system dry run can regenerate an unprotected tree.
Unknown path macros and unreadable preset graphs fail before running CMake.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re

from .errors import UsageError
from .locks import canonical


MACRO = re.compile(r'\$(\w*)\{([^}]*)\}')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key {key!r}')
        result[key] = value
    return result


@dataclass(frozen=True)
class Value:
    text: object
    file: Path
    expanded: bool = False


class Presets:
    def __init__(self, source, env):
        # CMake preserves a valid logical PWD, including sourceDirName. Only
        # canonicalize the resolved resource after expanding those macros.
        self.source = Path(os.path.abspath(source))
        self.env = env
        self.entries = {'configurePresets': {}, 'buildPresets': {}}
        self.loaded = set()
        for name in ('CMakePresets.json', 'CMakeUserPresets.json'):
            path = self.source / name
            if path.exists():
                self.load(path, ())
        if not self.loaded:
            raise UsageError(f'Cannot verify CMake target: no CMakePresets.json or '
                             f'CMakeUserPresets.json in {self.source}. Run from the source worktree.')

    def load(self, path, trail):
        path = Path(os.path.abspath(path))
        identity = canonical(path)
        if identity in trail:
            raise UsageError(f'CMake preset include cycle: {path}')
        if identity in self.loaded:
            return
        try:
            document = json.loads(path.read_text(), object_pairs_hook=unique_object)
            if not isinstance(document, dict):
                raise ValueError('expected a JSON object')
            for kind in self.entries:
                for entry in document.get(kind, []):
                    name = entry['name']
                    if not isinstance(name, str) or not name:
                        raise ValueError('preset name must be a non-empty string')
                    if name in self.entries[kind]:
                        raise ValueError(f'duplicate {kind} name {name!r}')
                    self.entries[kind][name] = (entry, path)
            for included in document.get('include', []):
                # Include expansion does not have a preset environment.
                included = self.expand(Value(included, path), '', {}, {}, ())
                self.load(path.parent / included, (*trail, identity))
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise UsageError(f'Cannot read CMake presets from {path}: {error}') from error
        self.loaded.add(identity)

    def inherit(self, kind, name, trail=()):
        if name in trail:
            raise UsageError(f'CMake preset inheritance cycle: {" -> ".join((*trail, name))}')
        try:
            entry, file = self.entries[kind][name]
        except KeyError as error:
            raise UsageError(f'Cannot verify CMake target: {kind} has no preset {name!r}. '
                             f'Check PRESET_MODE and the preset files in {self.source}.') from error
        parents = entry.get('inherits', [])
        if isinstance(parents, str):
            parents = [parents]
        if not isinstance(parents, list) or any(not isinstance(parent, str) for parent in parents):
            raise UsageError(f'Invalid inherits for CMake preset {name!r} in {file}')
        result = {}
        for parent in reversed(parents):
            self.merge(result, self.inherit(kind, parent, (*trail, name)))
        own = {}
        for key, value in entry.items():
            if key in ('name', 'hidden', 'inherits', 'description', 'displayName'):
                continue
            if key in ('environment', 'cacheVariables') and not isinstance(value, dict):
                raise UsageError(f'Invalid {key} for CMake preset {name!r} in {file}: expected an object')
            own[key] = ({subkey: Value(subvalue, file) for subkey, subvalue in value.items()}
                        if key in ('environment', 'cacheVariables') else Value(value, file))
        self.merge(result, own)
        result['_context_file'] = Value(None, file)
        return result

    @staticmethod
    def merge(target, values):
        for key, value in values.items():
            target[key] = ({**target.get(key, {}), **value}
                           if key in ('environment', 'cacheVariables') else value)

    def expand(self, value, name, fields, environment, trail):
        text = value.text
        if not isinstance(text, str):
            raise UsageError(f'Cannot resolve CMake path in {value.file}: expected a string')
        if value.expanded:
            return text
        constants = {'sourceDir': str(self.source), 'sourceParentDir': str(self.source.parent),
                     'sourceDirName': self.source.name, 'presetName': name,
                     'hostSystemName': platform.system(), 'fileDir': str(value.file.parent),
                     'dollar': '$', 'pathListSep': os.pathsep}

        def replace(match):
            namespace, key = match.groups()
            if namespace == 'penv':
                return self.env.get(key, '')
            if namespace == 'env':
                if key in trail:
                    raise UsageError(f'CMake preset environment cycle: {" -> ".join((*trail, key))}')
                if key not in environment:
                    return self.env.get(key, '')
                selected = environment[key]
                # A null entry cancels the preset override. CMake still sees
                # the inherited process environment for this macro.
                return (self.env.get(key, '') if selected.text is None else
                        self.expand(selected, name, fields, environment, (*trail, key)))
            if not namespace and key == 'generator' and 'generator' in fields:
                return str(fields['generator'].text)
            if not namespace and key == 'fileDir' and '_context_file' in fields:
                context = fields['_context_file'].file
                if context.parent != value.file.parent:
                    raise UsageError('Cannot safely resolve inherited ${fileDir} across preset directories: '
                                     f'{value.file} -> {context}. CMake versions differ in this expansion. '
                                     'Use ${sourceDir} or an absolute path before retrying.')
            if not namespace and key in constants:
                return constants[key]
            raise UsageError(f'Cannot verify CMake path: unsupported macro {match[0]!r} '
                             f'in preset {name!r}, file {value.file}. Use an explicit supported path.')

        # Do not re-expand substituted environment strings or ${dollar}.
        result = MACRO.sub(replace, text)
        if re.search(r'\$\w*\{[^}]*$', text):
            raise UsageError(f'Unclosed CMake path macro in {value.file}: {text!r}')
        return result

    def build_directory(self, name, *, configure=False):
        if not configure:
            build = self.inherit('buildPresets', name)
            options = build.get('nativeToolOptions', Value([], self.source / 'CMakePresets.json')).text
            if not isinstance(options, list) or any(not isinstance(option, str) for option in options):
                raise UsageError(f'Build preset {name!r} has invalid nativeToolOptions. Expected a string array.')
            for option in options:
                long_name = option.split('=', 1)[0]
                directory_override = option.startswith(('-C', '-f')) or (
                    len(long_name) > 2 and long_name.startswith('--') and any(name.startswith(long_name) for name in
                                                      ('--directory', '--file', '--makefile')))
                if (MACRO.search(option) or
                        directory_override or
                        re.match(r'DESTDIR\s*[:?+]?=', option)):
                    raise UsageError(f'Cannot verify the locked build/install paths with nativeToolOptions '
                                     f'{option!r} in build preset {name!r}. '
                                     'Use literal native options without directory/file overrides or DESTDIR before retrying.')
            if 'configurePreset' not in build or not isinstance(build['configurePreset'].text, str):
                raise UsageError(f'Build preset {name!r} has no configurePreset. Cannot verify its build path.')
            name = build['configurePreset'].text
        fields = self.inherit('configurePresets', name)
        value = fields.get('binaryDir', Value(str(self.source), self.source / 'CMakePresets.json'))
        directory = self.expand(value, name, fields, fields.get('environment', {}), ())
        if not directory:
            raise UsageError(f'CMake preset {name!r} resolves to an empty binaryDir')
        return canonical(self.source / directory)

    def verify_install_environment(self, name):
        build = self.inherit('buildPresets', name)
        configure_name = build['configurePreset'].text
        configure = self.inherit('configurePresets', configure_name)
        environment = dict(build.get('environment', {}))
        inherit = build.get('inheritConfigureEnvironment', Value(True, self.source)).text
        if not isinstance(inherit, bool):
            raise UsageError(f'Invalid inheritConfigureEnvironment in build preset {name!r}. Expected true or false.')
        own, file = self.entries['buildPresets'][name]
        own_environment = own.get('environment', {})
        configure_env = configure.get('environment', {})
        if not self.env.get('DESTDIR') and (
                ('DESTDIR' in own_environment and own_environment['DESTDIR'] in (None, '')) or
                ('DESTDIR' not in environment and (not inherit or 'DESTDIR' not in configure_env))):
            return
        if inherit:
            for key, value in configure_env.items():
                environment[key] = (value if value.text is None else
                                    Value(self.expand(value, configure_name, configure, configure_env, ()),
                                          value.file, expanded=True))
        # CMake applies the selected build preset's own environment last.
        environment.update({key: Value(value, file) for key, value in own_environment.items()})
        value = environment.get('DESTDIR')
        fields = dict(build)
        if 'generator' in configure:
            fields['generator'] = configure['generator']
        destdir = (self.expand(value, name, fields, environment, ())
                   if value is not None and value.text is not None else self.env.get('DESTDIR', ''))
        verify_destdir({'DESTDIR': destdir}, f'build preset {name!r}')


def verify_destdir(env, source='process environment'):
    if env.get('DESTDIR'):
        raise UsageError(f'DESTDIR in the {source} redirects installation outside the locked CUBRID prefix. '
                         'Remove DESTDIR before retrying. No installation command was run.')


def verify_build(env, expected, *, configure=False, installation_target=False):
    presets = Presets(env['PWD'], env)
    actual = presets.build_directory(env['PRESET_MODE'], configure=configure)
    if actual != expected:
        raise UsageError('CMake build directory mismatch. No CMake command was run.\n'
                         f'  Locked CUBRID_BUILD_DIR: {expected}\n'
                         f'  Preset binaryDir:       {actual}\n'
                         f'  PRESET_MODE:            {env["PRESET_MODE"]}\n'
                         'Select the matching preset/build directory before retrying.')
    if installation_target:
        presets.verify_install_environment(env['PRESET_MODE'])


def verify_install_prefix(build, expected):
    cache = Path(build) / 'CMakeCache.txt'
    try:
        values = {}
        for line in cache.read_text(errors='surrogateescape').splitlines():
            if line.startswith('//') or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key.split(':', 1)[0]] = value
    except OSError as error:
        raise UsageError(f'Cannot verify CMAKE_INSTALL_PREFIX in {cache}: {error}. '
                         'Configure this build directory before running an installation target.') from error
    prefix = values.get('CMAKE_INSTALL_PREFIX')
    if not prefix or not Path(prefix).is_absolute():
        raise UsageError(f'Cannot verify an absolute CMAKE_INSTALL_PREFIX in {cache}. '
                         'Configure it to match CUBRID before running the target.')
    actual = canonical(prefix)
    staging = values.get('CMAKE_STAGING_PREFIX')
    if staging and (not Path(staging).is_absolute() or canonical(staging) != expected):
        raise UsageError('CMake staging directory mismatch. The target was not run.\n'
                         f'  Locked CUBRID:        {expected}\n'
                         f'  CMAKE_STAGING_PREFIX: {staging}\n'
                         f'  Cache:                {cache}\n'
                         'Remove the staging prefix or configure it to match CUBRID before retrying.')
    if actual != expected:
        raise UsageError('CMake installation directory mismatch. The target was not run.\n'
                         f'  Locked CUBRID:        {expected}\n'
                         f'  CMAKE_INSTALL_PREFIX: {actual}\n'
                         f'  Cache:                {cache}\n'
                         'Reconfigure with the matching installation prefix before retrying.')
