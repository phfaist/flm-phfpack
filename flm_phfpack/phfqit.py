import re
import logging
logger = logging.getLogger(__name__)

from pylatexenc.latexnodes.nodes import (
    LatexCharsNode,
    LatexGroupNode,
)

from pylatexenc.latexnodes import (
    SingleParsedArgumentInfo,
    LatexWalkerLocatedError,
)
from pylatexenc.latexnodes.parsers import (
    LatexParserBase
)

from flm.feature import SimpleLatexDefinitionsFeature

from flm.flmspecinfo import FLMArgumentSpec, FLMMacroSpecBase

from flm.feature.substmacros import (
    SetArgumentNumberOffset,
    SubstitutionCallableSpecInfo,
    MacroContentSubstitutor,
    MacroContentSubstitutorManager,
)






# ------------------------------------------------------------------------------


class StrictSizeargTokenParser(LatexParserBase):
    
    def parse(self, latex_walker, token_reader, parsing_state, **kwargs):

        token = token_reader.next_token(parsing_state=parsing_state)

        size_arg = None

        if token.tok == 'macro':
            size_arg = '\\' + token.arg
        elif token.tok == 'char' and token.arg.strip() == '*':
            size_arg = token.arg.strip()

        if size_arg is not None:
            # yes, got a size arg.  Return it as a CHARS NODE for now.

            logger.debug("Found a sizearg token - %r", token)

            nodelist = latex_walker.make_nodelist(
                [
                    latex_walker.make_node(
                        LatexCharsNode,
                        parsing_state=parsing_state,
                        chars=size_arg,
                        pos=token.pos,
                        pos_end=token.pos_end,
                    )
                ],
                parsing_state=parsing_state,
            )
            return nodelist, None

        logger.debug("No sizearg token found :( token was - %r", token)

        # undo read token, return None
        token_reader.move_to_token(token)
        return None, None



# ------------------------------------------------------------------------------


rx_delim = re.compile(
    r'^Delim(?P<delimwhich>Left|Middle|Right)$'
)


map_delimiters_open = {
    '{': r'\{',
    '<': r'\langle',
}
map_delimiters_close = {
    '}': r'\}',
    '>': r'\rangle',
}


class PhfqitMacroContentSubstitutor(MacroContentSubstitutor):

    def initialize(self):

        # parse any delimiter sizing macros
        dl, dm, dr = self.get_delims_patterns(self.parsed_arguments_infos)
        self.delim_patterns = {'Left': dl, 'Middle': dm, 'Right': dr}

        # store delimiters for the "main delimited argument", if applicable:
        self.main_delimited_argument_delimiters = {'Left': '', 'Middle': '', 'Right': ''}
        if 'MainDelimitedArgument' in self.parsed_arguments_infos:
            argnode = \
                self.parsed_arguments_infos['MainDelimitedArgument'].argument_node_object
            if not argnode.isNodeType(LatexGroupNode):
                raise ValueError("Expected group node??")
            od, cd = argnode.delimiters
            self.main_delimited_argument_delimiters = {
                'Left': map_delimiters_open.get(od, od),
                'Middle': '|',
                'Right': map_delimiters_close.get(cd, cd),
            }


    def get_placeholder_value(self, placeholder_ref, *, substitution_arg_info, **kwargs):

        m = rx_delim.match(placeholder_ref)
        if m is not None:
            delimwhich = m.group('delimwhich')
            delimval = substitution_arg_info.get_content_nodelist().latex_verbatim()
            if not len(delimval):
                delimval = self.main_delimited_argument_delimiters[delimwhich]
            return (
                self.delim_patterns[delimwhich] % delimval
            )
        
        return super().get_placeholder_value(
            placeholder_ref,
            substitution_arg_info=substitution_arg_info,
            **kwargs
        )



    # ---

    def get_delims_patterns(self, parsed_arguments_infos):

        delimsizes = None

        def _error_multiple_delimiters(whicharg):
            raise LatexWalkerLocatedError(
                "Multiple sizing arguments provided",
                pos=parsed_arguments_infos[whicharg].argument_node_object.pos
            )

        if '_sizeargBacktick' in parsed_arguments_infos \
           and parsed_arguments_infos['_sizeargBacktick'].was_provided():
            argnodes = parsed_arguments_infos['_sizeargBacktick'].get_content_nodelist()
            if len(argnodes) != 1:
                raise ValueError("Expected exactly one argument after "
                                 "backtick (`) delimiter size specifier, got "
                                 + repr(argnodes))
            argnodes = argnodes[0].nodelist
            logger.debug('got backtick size arg: %r', argnodes)
            arg_s = argnodes.latex_verbatim()
            delimsizes = (r'' + arg_s + 'l %s',
                          r'\mathclose{}' + arg_s + ' %s\mathopen{}',
                          arg_s + 'r %s') # \bigl(, \big|, \bigr)
            
        use_star_from_arg = False
        use_star_from_star = False

        if '_sizeargArg' in parsed_arguments_infos \
           and parsed_arguments_infos['_sizeargArg'].was_provided():
            if delimsizes:
                _error_multiple_delimiters('_sizeargArg')
            argnodes = parsed_arguments_infos['_sizeargArg'].get_content_nodelist()
            logger.debug('got explicit size arg: %r', argnodes)
            if argnodes and len(argnodes) == 1 and argnodes[0].isNodeType(LatexCharsNode) \
               and argnodes[0].chars.strip() == '*':
                # single star as argument -> use \left/\right
                use_star_from_arg = True
            else:
                arg_s = argnodes.latex_verbatim()
                delimsizes = (arg_s + 'l %s',
                              r'\mathclose{}' + arg_s + ' %s\mathopen{}',
                              arg_s + 'r %s') # \bigl(, \big|, \bigr)

        if '_sizeargStar' in parsed_arguments_infos \
           and parsed_arguments_infos['_sizeargStar'].was_provided():

            logger.debug('got star size arg!')
            if delimsizes:
                _error_multiple_delimiters('_sizeargBacktick')
            use_star_from_star = True

        if use_star_from_arg or use_star_from_star:
            delimsizes = (r'\mathopen{}\left %s',
                          r'\mathclose{}\middle %s\mathopen{}',
                          r'\right %s\mathclose{}')

        if delimsizes is None:
            delimsizes = ('%s', '%s', '%s')

        return delimsizes
            


class PhfqitMacroContentSubstitutorManager(MacroContentSubstitutorManager):

    MacroContentSubstitutorClass = PhfqitMacroContentSubstitutor

    def __init__(self, *, config_values=None, **kwargs):
        super().__init__(**kwargs)

        # Set up config values.  Start with defaults, and update with given
        # settings.
        self.config_values = {
            'spaceKets': {
                'Bar': r'\mkern 1.5mu ',
                'RLAngle': r'\mkern -1.8mu ',
            },
            'spaceOKets': {
                'Bar': r'\mkern 1.5mu ',
                'RLAngle': r'\mkern -1.8mu ',
            },
            'space': {
                'BeforeComma': r'',
                'AfterComma': r'\mkern 1.5mu ',
            }
        }
        if config_values is not None:
            for crkey in self.config_values.keys():
                if crkey in config_values:
                    self.config_values[crkey].update(config_values[crkey])



    def get_placeholder_value(self, placeholder_ref, *, substitution_arg_info, **kwargs):

        if placeholder_ref == 'config':
            configroot, configkey = \
                substitution_arg_info.get_content_nodelist() \
                                     .latex_verbatim().split('.', maxsplit=1)
            return self.config_values[configroot][configkey]

        return super().get_placeholder_value(
            placeholder_ref,
            substitution_arg_info=substitution_arg_info,
            **kwargs
        )





class PhfqitSubstitutionCallable(SubstitutionCallableSpecInfo):

    MacroContentSubstitutorManagerClass = PhfqitMacroContentSubstitutorManager




# ------------------------------------------------------------------------------



_delims_spec_by_type = {
    'SizeArgBacktick': FLMArgumentSpec(
        "e{`}",
        argname='_sizeargBacktick',
    ),
    'SizeArgStar': FLMArgumentSpec(
        "*",
        argname='_sizeargStar',
    ),
    'SizeArgOptArg': FLMArgumentSpec(
        "[",
        argname='_sizeargArg',
    ),
}


_delims_spec_list = [
    _delims_spec_by_type['SizeArgBacktick'],
    _delims_spec_by_type['SizeArgStar'],
    _delims_spec_by_type['SizeArgOptArg'],
]

def _spec_delim(delimspec):
    # ('<', '>')   -> r'#[<]{DelimLeft}{#{Main}}#[>]{DelimRight}'
    # ('<', '..#{Arg1}#{Arg2}..', '>') -> r'#[<]{DelimLeft}..#{Arg1}#{Arg2}..#[>]{DelimRight}'

    if len(delimspec) == 2:
        leftd, rightd = delimspec
        replarg = '{#1}'
        numargs = 1
    elif len(delimspec) == 3:
        leftd, replarg, rightd = delimspec
        numargs = 1
    elif len(delimspec) == 4:
        numargs, leftd, replarg, rightd = delimspec
    else:
        raise ValueError("delimspec: " + repr(delimspec))

    content = (
        r'#[{' + leftd + r'}]{DelimLeft}'
        + replarg
        + r'#[{' + rightd + r'}]{DelimRight}'
    )

    return {
        'arguments_spec_list': (
            _delims_spec_list
            + [SetArgumentNumberOffset]
            + [{'parser':'{'} for _ in range(numargs)]
        ),
        'content': content,
    }


_defs_delim_specs = {
    'abs': (r'\lvert', r'\rvert'),
    'norm': (r'\lVert', r'\rVert'),
    'avg': (r'\langle', r'\rangle'),

    'ket': (r'\lvert', r'{#1}', r'\rangle'),
    'bra': (r'\langle', r'{#1}', r'\rvert'),
    'braket': (2,
               r'\langle',
               r'{#1}#[spaceKets.Bar]{config}#[\vert]{DelimMiddle}#[spaceKets.Bar]{config}{#2}',
               r'\rangle'),
    'ketbra': (2,
               r'\lvert',
               r'{#1}#[\rangle]{DelimMiddle} #[spaceKets.RLAngle]{config}#[\langle]{DelimMiddle}{#2}',
               r'\rvert'),
    'proj': (r'\lvert',
             r'{#1}#[\rangle]{DelimMiddle} #[spaceKets.RLAngle]{config}#[\langle]{DelimMiddle}{#1}',
             r'\rvert'),
    
    'matrixel': (3,
                 r'\langle',
                 r'{#1}#[spaceKets.Bar]{config}#[\vert]{DelimMiddle} #[spaceKets.Bar]{config}{#2}'
                 +r'#[spaceKets.Bar]{config}#[\vert]{DelimMiddle} #[spaceKets.Bar]{config}{#3}',
                 r'\rangle'),
    'dmatrixel': (2,
                  r'\langle',
                  r'{#1}#[spaceKets.Bar]{config}#[\vert]{DelimMiddle} #[spaceKets.Bar]{config}{#2}'
                 +r'#[spaceKets.Bar]{config}#[\vert]{DelimMiddle} #[spaceKets.Bar]{config}{#1}',
                  r'\rangle'),
    'innerprod': (2,
                  r'\langle',
                  r'{#1}#[space.BeforeComma]{config},#[space.AfterComma]{config}{#2}',
                  r'\rangle'),

    'oket': (r'\lvert', r'{#1}', r'\rrangle'),
    'obra': (r'\llangle', r'{#1}', r'\rvert'),
    'obraket': (2,
                r'\llangle',
                r'{#1}#[spaceOKets.Bar]{config}#[\vert]{DelimMiddle} #[spaceOKets.Bar]{config}{#2}',
               r'\rrangle'),
    'oketbra': (2,
                r'\lvert',
                r'{#1}#[\rrangle]{DelimMiddle} #[spaceOKets.RLAngle]{config}#[\llangle]{DelimMiddle}{#2}',
               r'\rvert'),
    'oproj': (r'\lvert',
              r'{#1}#[\rrangle]{DelimMiddle:} #[spaceOKets.RLAngle]{config}#[\llangle]{DelimMiddle}{#1}',
             r'\rvert'),
    
    'omatrixel': (3,
                  r'\llangle',
                  r'{#1}#[spaceOKets.Bar]{config}#[\vert]{DelimMiddle} #[spaceOKets.Bar]{config}{#2}'
                  + r'#[spaceOKets.Bar]{config}#[\vert]{DelimMiddle} #[spaceOKets.Bar]{config}{#3}',
                  r'\rrangle'),
    'odmatrixel': (2,
                   r'\llangle',
                   r'{#1}#[spaceOKets.Bar]{config}#[\vert]{DelimMiddle} #[spaceOKets.Bar]{config}{#2}'
                   + r'#[spaceOKets.Bar]{config}#[\vert]{DelimMiddle} #[spaceOKets.Bar]{config}{#1}',
                   r'\rrangle'),

    'intervalc': (2, r'[', r'{#1\mathclose{},\mathopen{}#2}', r']'),
    'intervalo': (2, r']', r'{#1\mathclose{},\mathopen{}#2}', r'['),
    'intervalco': (2, r'[', r'{#1\mathclose{},\mathopen{}#2}', r'['),
    'intervaloc': (2, r']', r'{#1\mathclose{},\mathopen{}#2}', r']'),

}

specs_delims = { k: _spec_delim(s)
                 for k,s in _defs_delim_specs.items() }




# ---

def _gate(x):
    return r'\textsc{'+x.lower()+r'}'
    #return r'\ifmmode\textsc{\lowercase{'+x+r'}}\else{\rmfamily\textsc{\lowercase{'+x+r'}}}\fi'


def _spec_simplesubst(substspec):
    if isinstance(substspec, str):
        substspec = { 'content': substspec }
    return substspec

_defs_substitution_macros = {
    r'Hs': r'\mathscr{H}',
    r'Ident': r'𝟙', #\mathds{1}',

    # bits and gates
    r'bit': {
        'arguments_spec_list': '{',
        'content': r'\texttt{#1}'
    },
    r'bitstring': {
        'arguments_spec_list': '{',
        'content': r'\ensuremath{\underline{\overline{\texttt{#1}}}}'
    },
    r'gate': {
        'arguments_spec_list': '{',
        'content': _gate("#1")
    },
    r'AND': _gate('And'),
    r'XOR': _gate('Xor'),
    r'CNOT': _gate('C-Not'),
    r'NOT': _gate('Not'),
    r'NOOP': _gate('No-Op'),

    # math groups
    'uu': dict(arguments_spec_list=['r()'], content=r'\mathrm{u}({#1})'),
    'UU': dict(arguments_spec_list=['r()'], content=r'\mathrm{U}({#1})'),
    'su': dict(arguments_spec_list=['r()'], content=r'\mathrm{su}({#1})'),
    'SU': dict(arguments_spec_list=['r()'], content=r'\mathrm{SU}({#1})'),
    'so': dict(arguments_spec_list=['r()'], content=r'\mathrm{so}({#1})'),
    'SO': dict(arguments_spec_list=['r()'], content=r'\mathrm{SO}({#1})'),
    'slalg': dict(arguments_spec_list=['r()'], content=r'\mathrm{sl}({#1})'),
    'SL': dict(arguments_spec_list=['r()'], content=r'\mathrm{SL}({#1})'),
    'GL': dict(arguments_spec_list=['r()'], content=r'\mathrm{GL}({#1})'),
    'SN': dict(arguments_spec_list=['r()'], content=r'\mathrm{S}_{#1}'),
}



specs_subst_macros = {
    k: _spec_simplesubst(s)
    for k,s in _defs_substitution_macros.items()
}


# ---


def _spec_ops(opspec):
    return { 'content': r'\operatorname{' + opspec + r'}' }


_defs_ops = {
    'tr': 'tr',
    'supp': 'supp',
    'rank': 'rank',
    'linspan': 'span',
    'spec': 'spec',
    'diag': 'diag',
    'Re': 'Re',
    'Im': 'Im',
    'poly': 'poly',
}

specs_ops = {
    k: _spec_ops(s)
    for k, s in _defs_ops.items()
}



# ---



# actually this is from the phfparen package, oh well, let's include it here,
# too.
specs_btick = {
    '`': {
        "arguments_spec_list": [
            {
                'parser': StrictSizeargTokenParser(),
                'argname': '_sizeargArg',
            },
            {
                'parser': 'AnyDelimited',
                'argname': 'MainDelimitedArgument'
            }
        ],
        "content": {
            'textmode': None,
            'mathmode': '#{DelimLeft}#{MainDelimitedArgument}#{DelimRight}',
        },
    }
}






# ------------------------------------------------------------------------------



class FeatureClass(SimpleLatexDefinitionsFeature):
    
    feature_name = 'phfqit'

    def __init__(self, macro_definitions=None):
        self.macro_definitions = macro_definitions or {}

    def add_latex_context_definitions(self):
        latex_definitions_macros = []
        latex_definitions_specials = []

        for macroname, spec in self.macro_definitions.items():

            if isinstance(spec, str):
                spec = {'content': spec}

            if 'delimited_arguments_spec_list' in spec:
                arguments_spec_list = (
                    _delims_spec_list
                    + [SetArgumentNumberOffset]
                    + spec['delimited_arguments_spec_list']
                )
            else:
                arguments_spec_list_init = spec.get('arguments_spec_list', [])
                arguments_spec_list = []
                for arg in arguments_spec_list_init:
                    if isinstance(arg, str) and arg in _delims_spec_by_type:
                        arguments_spec_list.append(
                            _delims_spec_by_type[arg]
                        )
                        continue
                        
                    # 'SetArgumentNumberOffset' is handled already in substmacros.py

                    # parse it as a normal argument spec ...
                    arguments_spec_list.append(arg)
                    

            latex_definitions_macros.append(
                PhfqitSubstitutionCallable(
                    macroname=macroname,
                    spec_node_parser_type='macro',
                    arguments_spec_list=arguments_spec_list,
                    content=spec['content']
                )
            )
        
        for k, v in specs_delims.items():
            latex_definitions_macros.append(
                PhfqitSubstitutionCallable(macroname=k, spec_node_parser_type='macro', **v)
            )
        for k, v in specs_subst_macros.items():
            latex_definitions_macros.append(
                PhfqitSubstitutionCallable(macroname=k, spec_node_parser_type='macro', **v)
            )
        for k, v in specs_ops.items():
            latex_definitions_macros.append(
                PhfqitSubstitutionCallable(macroname=k, spec_node_parser_type='macro', **v)
            )
        for k, v in specs_btick.items():
            latex_definitions_specials.append(
                PhfqitSubstitutionCallable(
                    specials_chars=k,
                    spec_node_parser_type='specials',
                    **v
                )
            )

        return {
            'macros': latex_definitions_macros,
            'specials': latex_definitions_specials,
        }
