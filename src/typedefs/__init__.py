"""共享数据模型 —— 与原项目 src.parser.models 兼容的 Variable 类型系统。"""

from dataclasses import dataclass, field
from typing import Optional, Union


@dataclass
class Symbol:
    name: str
    address: int
    size: int
    binding: str = "GLOBAL"
    sym_type: str = "OBJECT"
    section: str = ".bss"


@dataclass
class BaseType:
    name: str
    byte_size: int
    encoding: str = ""


@dataclass
class MemberInfo:
    name: str
    offset: int
    type_info: "TypeInfo"
    bit_size: int = 0
    bit_offset: int = 0


@dataclass
class StructType:
    name: str
    size: int
    members: list[MemberInfo] = field(default_factory=list)
    is_union: bool = False


@dataclass
class ArrayType:
    element_type: "TypeInfo"
    count: int
    total_size: int


@dataclass
class PointerType:
    pointed_type: Optional["TypeInfo"]
    size: int = 4


@dataclass
class EnumType:
    name: str
    size: int
    values: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class TypedefType:
    name: str
    underlying_type: "TypeInfo"


@dataclass
class FuncType:
    return_type: Optional["TypeInfo"] = None
    param_types: list["TypeInfo"] = field(default_factory=list)


TypeInfo = Union[BaseType, StructType, ArrayType, PointerType, EnumType, TypedefType, FuncType]


@dataclass
class Variable:
    name: str
    address: int
    size: int
    type_info: Optional[TypeInfo] = None
    symbol: Optional[Symbol] = None
    file_name: str = ""