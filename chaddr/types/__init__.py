"""Address type handlers — one module per profile type."""

from __future__ import annotations

from chaddr.types.base import AddressTypeHandler, DiagnoseItem, DiagnoseResult, ReallocateResult
from chaddr.profile_lexer import canonical_ws_tokens
from chaddr.types.aws_elastic_ip import AwsElasticIpHandler
from chaddr.types.aliyun_elastic_ip import AliyunElasticIpHandler
from chaddr.types.registered_nameserver import RegisteredNameserverHandler
from chaddr.types.hosts_file import HostsFileHandler
from chaddr.types.bind_db import BindDbHandler

HANDLERS: dict[str, type[AddressTypeHandler]] = {
    "aws elastic ip": AwsElasticIpHandler,
    "aliyun elastic ip": AliyunElasticIpHandler,
    "registered nameserver": RegisteredNameserverHandler,
    "hosts file": HostsFileHandler,
    "zone file": BindDbHandler,
    "bind db": BindDbHandler,
}


def get_handler_class(type_name: str) -> type[AddressTypeHandler] | None:
    return HANDLERS.get(canonical_ws_tokens(type_name).lower())


def create_handler(type_name: str, config: dict, options: dict, proxy, logger) -> AddressTypeHandler:
    handler_cls = get_handler_class(type_name)
    if handler_cls is None:
        raise ValueError(f"unsupported profile type: {type_name}")
    return handler_cls(config=config, options=options, proxy=proxy, logger=logger)


__all__ = [
    "HANDLERS",
    "AddressTypeHandler",
    "DiagnoseItem",
    "DiagnoseResult",
    "ReallocateResult",
    "create_handler",
    "get_handler_class",
]
