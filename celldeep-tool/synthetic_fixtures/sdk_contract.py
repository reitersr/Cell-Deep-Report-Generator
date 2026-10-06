"""Check what the pipeline sends to the Anthropic SDK against the SDK actually installed, so an argument the SDK
does not accept fails in the offline test suite instead of in production.

Every mocked Anthropic client in the tests calls check_create_kwargs() before answering. It binds the call to
the real `anthropic.resources.messages.Messages.create` signature (an unknown keyword raises the same
TypeError the SDK raises) and validates nested arguments against the SDK's own TypedDict parameter types
(unknown keys, missing required keys and invalid literal values all fail)."""

import inspect
import types
import typing

import anthropic
from anthropic.resources.messages import Messages
from anthropic.types import MessageParam, OutputConfigParam

CREATE_SIGNATURE = inspect.signature(Messages.create)
CLIENT_SIGNATURE = inspect.signature(anthropic.Anthropic.__init__)
_TYPED = {"output_config": OutputConfigParam}


class SDKContractError(TypeError):
    pass


def _is_typed_dict(hint) -> bool:
    return isinstance(hint, type) and issubclass(hint, dict) and hasattr(hint, "__required_keys__")


def _required_keys(typed_dict, hints) -> set:
    """Required keys from the resolved hints: the SDK declares TypedDicts with postponed annotations, for
    which Python's own __required_keys__ misses Required[...] fields of a total=False TypedDict."""
    total = getattr(typed_dict, "__total__", True)
    required = set()
    for key, hint in hints.items():
        origin = typing.get_origin(hint)
        if origin is typing.Annotated:
            origin = typing.get_origin(typing.get_args(hint)[0])
        if origin is typing.Required or (total and origin is not typing.NotRequired):
            required.add(key)
    return required


def _unwrap(hint):
    origin = typing.get_origin(hint)
    if origin in (typing.Required, typing.NotRequired, typing.Annotated):
        return _unwrap(typing.get_args(hint)[0])
    return hint


def _check(value, hint, where):
    hint = _unwrap(hint)
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if hint is typing.Any or hint is object:
        return
    if origin in (typing.Union, types.UnionType):
        errors = []
        for option in args:
            try:
                _check(value, option, where)
                return
            except SDKContractError as error:
                errors.append(str(error))
        raise SDKContractError(f"{where}: {value!r} matches none of the SDK's allowed shapes ({'; '.join(errors)})")
    if hint is type(None):
        if value is not None:
            raise SDKContractError(f"{where}: expected null")
        return
    if origin is typing.Literal:
        if value not in args:
            raise SDKContractError(f"{where}: {value!r} is not one of {args}")
        return
    if _is_typed_dict(hint):
        if not isinstance(value, dict):
            raise SDKContractError(f"{where}: expected an object for {hint.__name__}")
        hints = typing.get_type_hints(hint, include_extras=True)
        unknown = set(value) - set(hints)
        missing = _required_keys(hint, hints) - set(value)
        if unknown or missing:
            raise SDKContractError(f"{where} ({hint.__name__}): unknown keys {sorted(unknown)}, "
                                   f"missing required keys {sorted(missing)}")
        for key, item in value.items():
            _check(item, hints[key], f"{where}.{key}")
        return
    if origin in (list, tuple, set) or getattr(origin, "__name__", "") in ("Iterable", "Sequence"):
        if isinstance(value, (str, bytes, dict)) or not hasattr(value, "__iter__"):
            raise SDKContractError(f"{where}: expected a list")
        for index, item in enumerate(value):
            _check(item, args[0] if args else typing.Any, f"{where}[{index}]")
        return
    if origin is dict or getattr(origin, "__name__", "") in ("Mapping", "MutableMapping"):
        if not isinstance(value, dict):
            raise SDKContractError(f"{where}: expected an object")
        return
    if isinstance(hint, type):  # str, int, float, bool, or an SDK model class: the value must be one
        if not isinstance(value, hint) or (hint is int and isinstance(value, bool)):
            raise SDKContractError(f"{where}: expected {hint.__name__}, got {type(value).__name__}")
    # Anything else (e.g. a TypeVar) is accepted as given.


def check_create_kwargs(kwargs: dict) -> None:
    """Raise if `client.messages.create(**kwargs)` would be rejected by the installed SDK's signature or types."""
    try:
        CREATE_SIGNATURE.bind(None, **kwargs)
    except TypeError as error:
        raise SDKContractError(f"Messages.create() would reject these arguments: {error}") from None
    for name, hint in _TYPED.items():
        if name in kwargs:
            _check(kwargs[name], hint, name)
    for index, message in enumerate(kwargs.get("messages", [])):
        _check(message, MessageParam, f"messages[{index}]")


def check_client_kwargs(kwargs: dict) -> None:
    """Raise if `anthropic.Anthropic(**kwargs)` would be rejected by the installed SDK."""
    try:
        CLIENT_SIGNATURE.bind(None, **kwargs)
    except TypeError as error:
        raise SDKContractError(f"Anthropic() would reject these arguments: {error}") from None
