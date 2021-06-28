# Copyright (C) 2014,2015 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function

import sys
from collections import namedtuple

from pyang import plugin

_COPYRIGHT_NOTICE = """
// Copyright (C) 2021 The RustyBGP Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
// implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
// Code generated by pyang. DO NOT EDIT.

"""

EQUAL_TYPE_LEAF = 0
EQUAL_TYPE_ARRAY = 1
EQUAL_TYPE_MAP = 2
EQUAL_TYPE_CONTAINER = 3


def pyang_plugin_init():
    plugin.register_plugin(RustPlugin())


class RustPlugin(plugin.PyangPlugin):

    def __init__(self, name=None):
        super(RustPlugin, self).__init__(name=name)
        self.multiple_modules = True

    def add_output_format(self, fmts):
        fmts['rust'] = self

    def emit(self, ctx, modules, fd):
        ctx.golang_identity_map = {}
        ctx.golang_typedef_map = {}
        ctx.golang_struct_def = []
        ctx.golang_struct_names = {}

        ctx.emitted_type_names = {}

        ctx.prefix_rel = {}
        ctx.module_deps = []
        for m in modules:
            check_module_deps(ctx, m)

        # visit yang statements
        visit_modules(ctx)

        # emit bgp_configs
        emit_go(ctx, fd)


def visit_modules(ctx):
    # visit typedef and identity
    for mod in ctx.module_deps:
        visit_typedef(ctx, mod)
        visit_identity(ctx, mod)

    # visit container
    for mod in ctx.module_deps:
        visit_children(ctx, mod, mod.i_children)


def emit_go(ctx, fd):
    ctx.golang_struct_def.reverse()
    done = set()

    # emit
    generate_header(fd)

    for mod in ctx.module_deps:
        if mod not in _module_excluded:
            emit_typedef(ctx, mod, fd)
            emit_identity(ctx, mod, fd)

    for struct in ctx.golang_struct_def:
        struct_name = struct.uniq_name
        if struct_name in done:
            continue
        emit_class_def(ctx, struct, struct_name, struct.module_prefix, fd)
        done.add(struct_name)


def check_module_deps(ctx, mod):
    own_prefix = mod.i_prefix
    for k, v in mod.i_prefixes.items():
        mod = ctx.get_module(v[0])
        if mod is None:
            continue

        if mod.i_prefix != own_prefix:
            check_module_deps(ctx, mod)

        ctx.prefix_rel[mod.i_prefix] = k
        if (mod not in ctx.module_deps
                and mod.i_modulename not in _module_excluded):
            ctx.module_deps.append(mod)


def dig_leafref(type_obj):
    reftype = type_obj.i_type_spec.i_target_node.search_one('type')
    if is_leafref(reftype):
        return dig_leafref(reftype)
    else:
        return reftype


def emit_class_def(ctx, stmt, struct_name, prefix, fd):
    if len(stmt.i_children) == 1 and is_list(stmt.i_children[0]):
        return

    print('// struct for container %s:%s.' % (prefix, stmt.arg), file=fd)
    emit_description(stmt, fd)
    print('#[derive(Deserialize, Debug, Default)]')
    print('#[serde(deny_unknown_fields)]')
    print('pub(crate) struct %s {' % convert_to_golang(struct_name), file=fd)

    equal_elems = []

    for child in stmt.i_children:

        if child.path in _path_exclude:
            continue

        container_or_list_name = child.uniq_name
        val_name_go = convert_to_golang(child.arg)
        child_prefix = get_orig_prefix(child.i_orig_module)
        tag_name = child.uniq_name.lower()
        equal_type = EQUAL_TYPE_LEAF
        equal_data = None
        print('// original -> %s:%s' % (child_prefix, container_or_list_name), file=fd)

        # case leaf
        if is_leaf(child):
            type_obj = child.search_one('type')
            type_name = type_obj.arg

            # case identityref
            if is_identityref(type_obj):
                emit_type_name = convert_to_golang(type_obj.search_one('base').arg.split(':')[-1])

            # case leafref
            elif is_leafref(type_obj):
                if type_obj.search_one('path').arg.startswith('../config'):
                    continue
                t = dig_leafref(type_obj)
                if is_translation_required(t):
                    print('// %s:%s\'s original type is %s.' % (child_prefix, container_or_list_name, t.arg), file=fd)
                    emit_type_name = translate_type(t.arg)
                elif is_identityref(t):
                    emit_type_name = convert_to_golang(t.search_one('base').arg.split(':')[-1])
                else:
                    emit_type_name = _builtins_type_translation_map[t.arg]

            # case embeded enumeration
            elif is_enum(type_obj):
                emit_type_name = val_name_go

            # case translation required
            elif is_translation_required(type_obj):
                print('// %s:%s\'s original type is %s.' % (child_prefix, container_or_list_name, type_name), file=fd)
                emit_type_name = translate_type(type_name)

            # case other primitives
            elif is_builtin_type(type_obj):
                emit_type_name = _builtins_type_translation_map[type_name]

            # default
            else:
                base_module = type_obj.i_orig_module.i_prefix
                t = lookup_typedef(ctx, base_module, type_name)
                # print(t.golang_name, file=sys.stderr)
                emit_type_name = t.golang_name

        # case 'case'
        if is_case(child):
            continue

        if is_choice(child) and is_enum_choice(child):
            emit_type_name = val_name_go

        # case leaflist
        if is_leaflist(child):
            type_obj = child.search_one('type')
            type_name = type_obj.arg
            val_name_go = val_name_go + 'List'
            tag_name += '-list'
            equal_type = EQUAL_TYPE_ARRAY

            # case leafref
            if is_leafref(type_obj):
                t = dig_leafref(type_obj)
                emit_type_name = 'Vec<%s>' % _builtins_type_translation_map[t.arg]

            # case identityref
            elif is_identityref(type_obj):
                emit_type_name = 'Vec<%s>' % convert_to_golang(type_obj.search_one('base').arg.split(':')[-1])

            # case translation required
            elif is_translation_required(type_obj):
                print('// original type is list of %s' % type_obj.arg, file=fd)
                emit_type_name = 'Vec<%s>' % translate_type(type_name)

            # case other primitives
            elif is_builtin_type(type_obj):
                emit_type_name = 'Vec<%s>' % _builtins_type_translation_map[type_name]

            # default
            else:
                base_module = type_obj.i_orig_module.i_prefix
                t = lookup_typedef(ctx, base_module, type_name)
                emit_type_name = 'Vec<%s>' % t.golang_name

        # case container
        elif is_container(child) or (is_choice(child) and not is_enum_choice(child)):
            key = child_prefix + ':' + container_or_list_name
            t = ctx.golang_struct_names[key]
            val_name_go = t.golang_name
            if len(t.i_children) == 1 and is_list(t.i_children[0]):
                c = t.i_children[0]
                emit_type_name = 'Vec<%s>' % c.golang_name
                equal_type = EQUAL_TYPE_MAP
                equal_data = c.search_one('key').arg
                leaf = c.search_one('leaf').search_one('type')
                if leaf.arg == 'leafref' and leaf.search_one('path').arg.startswith('../config'):
                    equal_data = 'config.' + equal_data
            else:
                emit_type_name = t.golang_name
                equal_type = EQUAL_TYPE_CONTAINER

        # case list
        elif is_list(child):
            key = child_prefix + ':' + container_or_list_name
            t = ctx.golang_struct_names[key]
            val_name_go = val_name_go + 'List'
            tag_name += '-list'
            emit_type_name = 'Vec<%s>' % t.golang_name
            equal_type = EQUAL_TYPE_MAP
            equal_data = child.search_one('key').arg

        if is_container(child):
            name = emit_type_name
            if name.startswith(convert_to_golang(struct_name)) and name.endswith("Config"):
                tag_name = 'config'
                val_name_go = 'Config'
            elif name.startswith(convert_to_golang(struct_name)) and name.endswith("State"):
                tag_name = 'state'
                val_name_go = 'State'

        emit_description(child, fd=fd)
        if '-' in tag_name:
            print('#[serde(rename = "%s")]' % tag_name)

        if tag_name == 'as':
            tag_name = 'r#as'
        print('pub(crate) {0}:Option<{1}>,'.format(tag_name.replace('-', '_'), emit_type_name), file=fd)

        equal_elems.append((val_name_go, emit_type_name, equal_type, equal_data))

    print('}', file=fd)


def get_orig_prefix(mod):
    orig = mod.i_orig_module
    if orig:
        get_orig_prefix(orig)
    else:
        return mod.i_prefix


def get_path(c):
    path = ''
    if c.parent is not None:
        p = ''
        if hasattr(c, 'i_module'):
            mod = c.i_module
            prefix = mod.search_one('prefix')
            if prefix:
                p = prefix.arg + ":"

        path = get_path(c.parent) + "/" + p + c.arg
    return path


# define container embedded enums
def define_enum(ctx, mod, c):
    prefix = mod.i_prefix
    c.path = get_path(c)
    c.golang_name = convert_to_golang(c.arg)
    if prefix in ctx.golang_typedef_map:
        ctx.golang_typedef_map[prefix][c.arg] = c
    else:
        ctx.golang_typedef_map[prefix] = {c.arg: c}


def visit_children(ctx, mod, children):
    for c in children:
        if is_case(c):
            prefix = get_orig_prefix(c.parent.i_orig_module)
            c.i_orig_module = c.parent.i_orig_module
        else:
            prefix = get_orig_prefix(c.i_orig_module)

        c.uniq_name = c.arg
        if c.arg == 'config':
            c.uniq_name = c.parent.uniq_name + '-config'

        elif c.arg == 'state':
            c.uniq_name = c.parent.uniq_name + '-state'

        elif c.arg == 'graceful-restart' and prefix == 'bgp-mp':
            c.uniq_name = 'mp-graceful-restart'

        if is_leaf(c) and is_enum(c.search_one('type')):
            define_enum(ctx, mod, c)

        elif is_list(c) or is_container(c) or is_choice(c):
            c.golang_name = convert_to_golang(c.uniq_name)

            if is_choice(c):
                picks = pickup_choice(c)
                c.i_children = picks
                if is_enum_choice(c):
                    define_enum(ctx, mod, c)
                    continue

            prefix_name = prefix + ':' + c.uniq_name
            if prefix_name in ctx.golang_struct_names:
                ext_c = ctx.golang_struct_names.get(prefix_name)
                ext_c_child_count = len(getattr(ext_c, "i_children"))
                current_c_child_count = len(getattr(c, "i_children"))
                if ext_c_child_count < current_c_child_count:
                    c.module_prefix = prefix
                    ctx.golang_struct_names[prefix_name] = c
                    idx = ctx.golang_struct_def.index(ext_c)
                    ctx.golang_struct_def[idx] = c
            else:
                c.module_prefix = prefix
                ctx.golang_struct_names[prefix_name] = c
                ctx.golang_struct_def.append(c)

        c.path = get_path(c)
        # print(c.path, file=sys.stderr)
        if hasattr(c, 'i_children'):
            visit_children(ctx, mod, c.i_children)


def pickup_choice(c):
    element = []
    for child in c.i_children:
        if is_case(child):
            element = element + child.i_children

    return element


def get_type_spec(stmt):
    for s in stmt.substmts:
        if hasattr(s, 'i_type_spec'):
            return s.i_type_spec.name

    return None


def visit_typedef(ctx, mod):
    prefix = mod.i_prefix
    child_map = {}
    for stmt in mod.substmts:
        if is_typedef(stmt):
            stmt.path = get_path(stmt)
            # print('stmt.path = "%s"' % stmt.path, file=sys.stderr)
            name = stmt.arg
            stmt.golang_name = convert_to_golang(name)
            # print('stmt.golang_name = "%s"' % stmt.golang_name, file=sys.stderr)
            child_map[name] = stmt

    ctx.golang_typedef_map[prefix] = child_map
    # print('ctx.golang_typedef_map["%s"] = %s' % (prefix, child_map), file=sys.stderr)
    prefix_rel = ctx.prefix_rel[prefix]
    ctx.golang_typedef_map[prefix_rel] = child_map
    # print('ctx.golang_typedef_map["%s"] = %s' % (prefix_rel, child_map)), file=sys.stderr)


def visit_identity(ctx, mod):
    prefix = mod.i_prefix
    child_map = {}
    for stmt in mod.substmts:
        if is_identity(stmt):
            name = stmt.arg
            stmt.golang_name = convert_to_golang(name)
            # print('stmt.golang_name = "%s"' % stmt.golang_name, file=sys.stderr)
            child_map[name] = stmt

            base = stmt.search_one('base')
            if base:
                base_name = base.arg
                if ':' in base_name:
                    base_prefix, base_name = base_name.split(':', 1)
                    if base_prefix in ctx.golang_identity_map:
                        ctx.golang_identity_map[base_prefix][base_name].substmts.append(stmt)
                else:
                    child_map[base_name].substmts.append(stmt)

    ctx.golang_identity_map[prefix] = child_map
    # print('ctx.golang_identity_map["%s"] = %s\n' % (prefix, child_map), file=sys.stderr)
    prefix_rel = ctx.prefix_rel[prefix]
    ctx.golang_identity_map[prefix_rel] = child_map
    # print('ctx.golang_identity_map["%s"] = %s\n' % (prefix_rel, child_map), file=sys.stderr)


def lookup_identity(ctx, default_prefix, identity_name):
    result = lookup(ctx.golang_identity_map, default_prefix, identity_name)
    return result


def lookup_typedef(ctx, default_prefix, type_name):
    result = lookup(ctx.golang_typedef_map, default_prefix, type_name)
    return result


def lookup(basemap, default_prefix, key):
    if ':' in key:
        pref, name = key.split(':')
    else:
        pref = default_prefix
        name = key

    if pref in basemap:
        return basemap[pref].get(name, None)
    else:
        return key


def emit_description(stmt, fd):
    desc = stmt.search_one('description')
    if desc is None:
        return None
    desc_words = desc.arg if desc.arg.endswith('.') else desc.arg + '.'
    print('// %s' % desc_words.replace('\n', '\n// '), file=fd)


def emit_enum(prefix, name, stmt, substmts, fd):
    type_name_org = name
    type_name = stmt.golang_name
    print('// typedef for identity %s:%s.' % (prefix, type_name_org), file=fd)
    emit_description(stmt, fd)

    const_prefix = convert_const_prefix(type_name_org)
    print('#[derive(Deserialize, Debug)]')
    print('#[serde(try_from = "String")]')
    print('pub(crate) enum %s {' % type_name, file=fd)
    m = {}

    if is_choice(stmt) and is_enum_choice(stmt):
        n = namedtuple('Statement', ['arg'])
        n.arg = 'none'
        substmts = [n] + substmts

    for sub in substmts:
        enum_name = convert_to_camelcase(sub.arg)
        m[sub.arg.lower()] = enum_name
        print('%s,' % enum_name, file=fd)
    print('}\n', file=fd)

    print('impl TryFrom<String> for %s {' % type_name)
    print('type Error = String;')
    print('fn try_from(s: String)->Result<Self, Self::Error> {')
    print('match s.as_str() {')
    for sub in substmts:
        enum_name = convert_to_camelcase(sub.arg)
        print('"%s" => Ok(Self::%s),' % (sub.arg.lower(), enum_name))
    print('_ => Err(format!("invalid parameter (%s) {}",s)),' % type_name)
    print('}\n}\n}')

    # if stmt.search_one('default'):
    #     default = stmt.search_one('default')
    #     print('func (v %s) Default() %s {' % (type_name, type_name), file=fd)
    #     print('return %s' % m[default.arg.lower()], file=fd)
    #     print('}\n', file=fd)

    #     print('func (v %s) DefaultAsNeeded() %s {' % (type_name, type_name), file=fd)
    #     print(' if string(v) == "" {', file=fd)
    #     print(' return v.Default()', file=fd)
    #     print('}', file=fd)
    #     print(' return v', file=fd)
    #     print('}', file=fd)

    #     print('func (v %s) ToInt() int {' % type_name, file=fd)
    #     print('_v := v.DefaultAsNeeded()')
    #     print('i, ok := %sToIntMap[_v]' % type_name, file=fd)

    # else:
    #     print('func (v %s) ToInt() int {' % type_name, file=fd)
    #     print('i, ok := %sToIntMap[v]' % type_name, file=fd)
    # print('if !ok {', file=fd)
    # print('return -1', file=fd)
    # print('}', file=fd)
    # print('return i', file=fd)
    # print('}', file=fd)


def emit_typedef(ctx, mod, fd):
    prefix = mod.i_prefix
    t_map = ctx.golang_typedef_map[prefix]
    for name, stmt in t_map.items():
        if stmt.path in _typedef_exclude:
            continue

        # skip identityref type because currently skip identity
        if get_type_spec(stmt) == 'identityref':
            continue

        type_name_org = name
        type_name = stmt.golang_name
        if type_name in ctx.emitted_type_names:
            print("warning %s: %s has already been emitted from %s."
                  % (prefix + ":" + type_name_org, type_name_org, ctx.emitted_type_names[type_name]),
                  file=sys.stderr)
            continue

        ctx.emitted_type_names[type_name] = prefix + ":" + type_name_org

        t = stmt.search_one('type')
        if not t and is_choice(stmt):
            emit_enum(prefix, type_name_org, stmt, stmt.i_children, fd)
        elif is_enum(t):
            emit_enum(prefix, type_name_org, stmt, t.substmts, fd)
        elif is_union(t):
            print('// typedef for typedef %s:%s.' % (prefix, type_name_org), file=fd)
            emit_description(t, fd)
            print('type %s = String;' % type_name, file=fd)
        else:
            if is_leafref(t):
                t = dig_leafref(t)

            print('// typedef for typedef %s:%s.' % (prefix, type_name_org), file=fd)
            if is_builtin_type(t):
                emit_description(t, fd)
                print('type %s = %s;' % (type_name, _builtins_type_translation_map[t.arg]), file=fd)
            elif is_translation_required(t):
                print('// %s:%s\'s original type is %s.' % (prefix, name, t.arg), file=fd)
                emit_description(t, fd)
                print('type %s = %s;' % (type_name, translate_type(t.arg)), file=fd)
            else:
                m = ctx.golang_typedef_map
                for k in t.arg.split(':'):
                    m = m[k]
                emit_description(t, fd)
                print('type %s = %s;' % (type_name, m.golang_name), file=fd)


def emit_identity(ctx, mod, fd):
    prefix = mod.i_prefix
    i_map = ctx.golang_identity_map[prefix]
    for name, stmt in i_map.items():
        enums = stmt.search('identity')
        if len(enums) > 0:
            emit_enum(prefix, name, stmt, enums, fd)


def is_reference(s):
    return s.arg in ['leafref', 'identityref']


def is_leafref(s):
    return s.arg in ['leafref']


def is_identityref(s):
    return s.arg in ['identityref']


def is_enum(s):
    return s.arg in ['enumeration']


def is_union(s):
    return s.arg in ['union']


def is_typedef(s):
    return s.keyword in ['typedef']


def is_identity(s):
    return s.keyword in ['identity']


def is_leaf(s):
    return s.keyword in ['leaf']


def is_leaflist(s):
    return s.keyword in ['leaf-list']


def is_list(s):
    return s.keyword in ['list']


def is_container(s):
    return s.keyword in ['container']


def is_case(s):
    return s.keyword in ['case']


def is_choice(s):
    return s.keyword in ['choice']


def is_enum_choice(s):
    return all(e.search_one('type').arg in _type_enum_case for e in s.i_children)


_type_enum_case = [
    'empty',
]


def is_builtin_type(t):
    return t.arg in _type_builtin


def is_translation_required(t):
    return t.arg in list(_type_translation_map.keys())


_type_translation_map = {
    'union': 'String',
    'decimal64': 'f64',
    'boolean': 'bool',
    'empty': 'bool',
    'inet:ip-address': 'String',
    'inet:ip-prefix': 'String',
    'inet:ipv4-address': 'String',
    'inet:as-number': 'u32',
    'bgp-set-community-option-type': 'String',
    'inet:port-number': 'u16',
    'yang:timeticks': 'i64',
    'ptypes:install-protocol-type': 'String',
    'binary': 'Vec<u8>',
    'route-family': 'u32',
    'bgp-capability': 'Vec<u8>',
    'bgp-open-message': 'Vec<u8>',
}

_builtins_type_translation_map = {
    'union': 'String',
    'int8': 'i8',
    'int16': 'i16',
    'int32': 'i32',
    'int64': 'i64',
    'string': 'String',
    'uint8': 'u8',
    'uint16': 'u16',
    'uint32': 'u32',
    'uint64': 'u64',
}

_type_builtin = [
    "union",
    "int8",
    "int16",
    "int32",
    "int64",
    "string",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
]


_module_excluded = [
    "ietf-inet-types",
    "ietf-yang-types",
]

_path_exclude = [
    "/rpol:routing-policy/rpol:defined-sets/rpol:neighbor-sets/rpol:neighbor-set/rpol:neighbor",
    "/rpol:routing-policy/rpol:defined-sets/bgp-pol:bgp-defined-sets/bgp-pol:community-sets/bgp-pol:community-set/bgp-pol:community-member",
    "/rpol:routing-policy/rpol:defined-sets/bgp-pol:bgp-defined-sets/bgp-pol:ext-community-sets/bgp-pol:ext-community-set/bgp-pol:ext-community-member",
    "/rpol:routing-policy/rpol:defined-sets/bgp-pol:bgp-defined-sets/bgp-pol:as-path-sets/bgp-pol:as-path-set/bgp-pol:as-path-set-member",
]

_typedef_exclude = [
    "/gobgp:bgp-capability",
    "/gobgp:bgp-open-message",
]


def generate_header(fd):
    print(_COPYRIGHT_NOTICE, file=fd)
    print('use serde::Deserialize;')
    print('use std::convert::TryFrom;')
    print('', file=fd)


def translate_type(key):
    if key in _type_translation_map.keys():
        return _type_translation_map[key]
    else:
        return key


# 'hoge-hoge' -> 'HogeHoge'
def convert_to_golang(type_string):
    a = type_string.split('.')
    return '.'.join(''.join(t.capitalize() for t in x.split('-')) for x in a)


# 'hoge-hoge' -> 'HOGE_HOGE'
def convert_const_prefix(type_string):
    return type_string.replace('-', '_').upper()


def chop_suf(s, suf):
    if not s.endswith(suf):
        return s
    return s[:-len(suf)]


def convert_to_camelcase(type_string):
    import re
    return ''.join(x.title() for x in re.split('_|-', type_string))
