from collections.abc import Sequence
from datetime import datetime
from typing import IO

from gofra.context import ProgramContext
from gofra.parser.intrinsics import Intrinsic
from gofra.parser.operators import Operator, OperatorType

from ._context import CodegenContext


def generate_ARM64_MacOS_backend(  # noqa: N802
    fd: IO[str],
    program_context: ProgramContext,
    *,
    debug_comments: bool,
) -> None:
    context = CodegenContext(fd=fd)

    if debug_comments:
        _write_debug_header_comment(context)

    # NOTICE: Linkage for extern function is not done in this backend
    # because it is not required for ARM64 MacOS
    # (linker automatically links extern functions)

    _write_function_declarations(
        context,
        program_context,
        debug_comments=debug_comments,
    )

    _write_entry_header(fd)

    _write_executable_body_instruction_set(
        fd,
        context,
        program_context.operators,
        program_context,
        debug_comments=debug_comments,
    )
    _write_program_epilogue(context, debug_comments=debug_comments)
    _write_static_segment(context)


def _write_executable_body_instruction_set(
    fd: IO[str],
    context: CodegenContext,
    operators: Sequence[Operator],
    program_context: ProgramContext,
    *,
    debug_comments: bool,
) -> None:
    for idx, operator in enumerate(operators):
        if debug_comments:
            _write_debug_operator_comment(context, operator)

        match operator.type:
            case OperatorType.PUSH_INTEGER:
                context.write(
                    "sub SP, SP, #16",
                    "mov X0, #%d" % operator.operand,
                    "str X0, [SP]",
                )
            case OperatorType.PUSH_STRING:
                assert isinstance(operator.operand, str)

                context.write(
                    "sub SP, SP, #16",
                    "adr X0, %s" % context.load_string(operator.token.text[1:-1]),
                    "str X0, [SP]",
                    "sub SP, SP, #16",
                    "mov X0, #%d" % len(operator.operand),
                    "str X0, [SP]",
                )
            case OperatorType.DO:
                assert isinstance(operator.jumps_to_operator_idx, int)
                context.write(
                    "ldr X0, [SP]",
                    "add SP, SP, #16",
                    "cmp X0, #1",
                    "bne .ctx_%s_over" % operator.jumps_to_operator_idx,
                )
            case OperatorType.END | OperatorType.WHILE:
                if isinstance(operator.jumps_to_operator_idx, int):
                    context.write(
                        "b .ctx_%s" % operator.jumps_to_operator_idx,
                    )
                    fd.write(f".ctx_{idx}_over:\n")
                else:
                    fd.write(f".ctx_{idx}:\n")
            case OperatorType.IF:
                assert isinstance(operator.jumps_to_operator_idx, int)
                context.write(
                    "ldr X0, [SP]",
                    "add SP, SP, #16",
                    "cmp X0, #1",
                    "bne .ctx_%s" % operator.jumps_to_operator_idx,
                )
            case OperatorType.INTRINSIC:
                assert isinstance(operator.operand, Intrinsic)
                match operator.operand:
                    case Intrinsic.MEMORY_LOAD:
                        context.write(
                            "ldr X0, [SP]",
                            "ldr X1, [X0]",
                            "str X1, [SP]",
                        )
                    case Intrinsic.MEMORY_STORE:
                        context.write(
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "ldr X1, [SP]",
                            "str X0, [X1]",
                        )
                    case Intrinsic.DROP:
                        context.write("add SP, SP, #16")
                    case (
                        Intrinsic.SYSCALL0
                        | Intrinsic.SYSCALL1
                        | Intrinsic.SYSCALL2
                        | Intrinsic.SYSCALL3
                        | Intrinsic.SYSCALL4
                        | Intrinsic.SYSCALL5
                        | Intrinsic.SYSCALL6
                    ):
                        syscall_arguments = operator.get_syscall_arguments_count()
                        injected_args = operator.syscall_optimization_injected_args or [
                            None for _ in range(syscall_arguments)
                        ]

                        if injected_args[-1] is None:
                            context.write(
                                "ldr X16, [SP]",
                                "add SP, SP, #16",
                            )
                        else:
                            context.write("mov X16, #%d" % injected_args[-1])

                        injected_args.pop()

                        for arg_n in range(syscall_arguments - 1):
                            # Load register in reversed order of stack so top of the stack is max register
                            arg_register = syscall_arguments - arg_n - 2

                            injected_arg = injected_args[-arg_n] if arg_n else None
                            if injected_arg is None:
                                context.write("ldr X%s, [SP]" % arg_register)
                                context.write("add SP, SP, #16")
                            else:
                                context.write(
                                    "mov X%s, #%d" % (arg_register, injected_arg),
                                )
                        context.write("svc #0")

                        if not operator.syscall_optimization_omit_result:
                            # Do not store result on stack if optimization is applied for omitting result
                            # this occurs when result drops after syscall
                            context.write(
                                "sub SP, SP, #16",
                                "str X0, [SP]",
                            )
                    case Intrinsic.PLUS:
                        context.write(
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "add X0, X1, X0",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.MINUS:
                        context.write(
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "sub X0, X1, X0",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.COPY:
                        context.write(
                            "ldr X0, [SP]",
                            "str X0, [SP]",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.INCREMENT:
                        context.write(
                            "ldr X0, [SP]",
                            "add X0, X0, #1",
                            "str X0, [SP]",
                        )
                    case Intrinsic.DECREMENT:
                        context.write(
                            "ldr X0, [SP]",
                            "sub X0, X0, #1",
                            "str X0, [SP]",
                        )
                    case Intrinsic.MULTIPLY:
                        context.write(
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "mul X0, X1, X0",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.DIVIDE:
                        context.write(
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "sdiv X0, X1, X0",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.MODULUS:
                        context.write(
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "udiv X2, X1, X0",
                            "mul X2, X2, X0",
                            "sub X0, X1, X2",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.NOT_EQUAL:
                        context.write(
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "cmp X0, X1",
                            "cset X0, ne",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.GREATER_EQUAL_THAN:
                        context.write(
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "cmp X0, X1",
                            "cset X0, ge",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.LESS_EQUAL_THAN:
                        context.write(
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "cmp X0, X1",
                            "cset X0, le",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.LESS_THAN:
                        context.write(
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "cmp X0, X1",
                            "cset X0, lt",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.GREATER_THAN:
                        context.write(
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "cmp X0, X1",
                            "cset X0, gt",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.EQUAL:
                        context.write(
                            "ldr X1, [SP]",
                            "add SP, SP, #16",
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "cmp X0, X1",
                            "cset X0, eq",
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                    case Intrinsic.SWAP:
                        context.write(
                            "ldr X0, [SP]",
                            "add SP, SP, #16",
                            "ldr X1, [SP]",
                            "str X0, [SP]",
                            "sub SP, SP, #16",
                            "str X1, [SP]",
                        )
            case OperatorType.CALL:
                assert isinstance(operator.operand, str)

                function_name = operator.operand
                if function_name in program_context.functions:
                    function = program_context.functions[function_name]
                    if function.is_externally_defined:
                        for arg_register in range(
                            len(function.type_contract_in) - 1,
                            -1,
                            -1,
                        ):
                            context.write("ldr X%s, [SP]" % arg_register)
                            context.write("add SP, SP, #16")
                    context.write("bl %s" % function_name)
                    if function.type_contract_out:
                        context.write(
                            "sub SP, SP, #16",
                            "str X0, [SP]",
                        )
                elif function_name in getattr(program_context, 'extern_functions', set()):
                    # MVP: только один аргумент (X0), можно расширить
                    context.write("ldr X0, [SP]")
                    context.write("add SP, SP, #16")
                    context.write("bl %s" % function_name)
                    context.write("sub SP, SP, #16")
                    context.write("str X0, [SP]")
                else:
                    raise KeyError(function_name)
            case _:
                raise NotImplementedError(
                    "Operator %s is not implemented in ARM64 MacOS backend"
                    % operator.type.name,
                )


def _write_debug_operator_comment(context: CodegenContext, operator: Operator) -> None:
    location = operator.token.location

    if operator.type == OperatorType.INTRINSIC:
        assert isinstance(operator.operand, Intrinsic)
        comment = "// * Intrinsic %s" % operator.operand.name
    else:
        comment = "// * Operator %s" % operator.type.name
    comment += " from %s" % location
    if operator.has_optimizations:
        if operator.is_syscall():
            comment += " [optimized, omit result: %s, injected args: %s]" % (
                operator.syscall_optimization_omit_result,
                operator.syscall_optimization_injected_args,
            )
        else:
            comment += " [optimized, infer type: %s]" % (
                operator.infer_type_after_optimization.name
                if operator.infer_type_after_optimization
                else "as-is"
            )

    context.write(comment)


def _write_function_declarations(
    context: CodegenContext,
    program_context: ProgramContext,
    *,
    debug_comments: bool,
) -> None:
    for function in filter(
        lambda f: not f.emit_inline_body and not f.is_externally_defined,
        program_context.functions.values(),
    ):
        context.fd.write("%s:\n" % function.name)
        _write_executable_body_instruction_set(
            context.fd,
            context,
            function.source,
            program_context,
            debug_comments=debug_comments,
        )
        context.write("ret")


def _write_debug_header_comment(context: CodegenContext) -> None:
    context.fd.write("// Assembly generated by Gofra codegen backend\n\n")
    context.fd.write("// Generated at: %s\n" % datetime.now(tz=None))  # noqa: DTZ005
    context.fd.write("// Target: ARM64, MacOS\n\n")


def _write_program_epilogue(context: CodegenContext, *, debug_comments: bool) -> None:
    if debug_comments:
        context.write(
            "// Program epilogue ",
            "// (exit return-code 0)",
            "// (always included)",
        )
    context.write(
        "mov X0, #0",
        "mov X16, #1",
        "svc #0",
    )


def _write_static_segment(context: CodegenContext) -> None:
    context.fd.write("mem_buffer: .space 1000\n")
    if not context.strings:
        return

    for string_key, string_value in context.strings.items():
        context.fd.write(f'{string_key}: .string "{string_value}"\n')


def _write_entry_header(fd: IO[str]) -> None:
    fd.write(".global _start\n")
    fd.write(".align 4\n\n")
    fd.write("_start:\n")
