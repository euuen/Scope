"""纯 Python 二进制 ELF 解析器（无外部依赖）。

直接解析 ELF 文件格式的二进制布局，无需 arm-none-eabi-readelf。

支持:
  - ELF header (32/64-bit, 大小端自适应)
  - Section header table (包含 .shstrtab 名称解析)
  - Symbol table (.symtab)
  - DWARF 行表 (.debug_line) 源文件列表
  - .data 节区初始值读取

参考自 old/src/parser/readelf.py 的算法和类型定义。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Optional

# ──────────────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────────────

# e_ident 索引
EI_CLASS = 4  # 1=32-bit, 2=64-bit
EI_DATA  = 5  # 1=小端, 2=大端

ELFCLASS32 = 1
ELFCLASS64 = 2
ELFDATA2LSB = 1
ELFDATA2MSB = 2

# e_type
ET_NONE = 0
ET_REL  = 1
ET_EXEC = 2
ET_DYN  = 3
ET_CORE = 4

ET_NAMES = {
    ET_NONE: "NONE",
    ET_REL:  "REL (Relocatable)",
    ET_EXEC: "EXEC (Executable)",
    ET_DYN:  "DYN (Shared)",
    ET_CORE: "CORE",
}

# e_machine
EM_NAMES = {
    0:   "EM_NONE",
    2:   "SPARC",
    3:   "386",
    8:   "MIPS",
    20:  "PPC",
    21:  "PPC64",
    22:  "S390",
    40:  "ARM",
    43:  "SPARCV9",
    50:  "IA_64",
    62:  "X86_64",
    183: "AARCH64",
    243: "RISCV",
    247: "CSKY",
}

# sh_type
SHT_NULL      = 0
SHT_PROGBITS  = 1
SHT_SYMTAB    = 2
SHT_STRTAB    = 3
SHT_RELA      = 4
SHT_HASH      = 5
SHT_DYNAMIC   = 6
SHT_NOTE      = 7
SHT_NOBITS    = 8
SHT_REL       = 9
SHT_DYNSYM    = 11
SHT_GROUP     = 17

SHT_NAMES = {
    SHT_NULL:     "NULL",
    SHT_PROGBITS: "PROGBITS",
    SHT_SYMTAB:   "SYMTAB",
    SHT_STRTAB:   "STRTAB",
    SHT_RELA:     "RELA",
    SHT_HASH:     "HASH",
    SHT_DYNAMIC:  "DYNAMIC",
    SHT_NOTE:     "NOTE",
    SHT_NOBITS:   "NOBITS",
    SHT_REL:      "REL",
    SHT_DYNSYM:   "DYNSYM",
    SHT_GROUP:    "GROUP",
}

# sh_flags
SHF_WRITE     = 0x1
SHF_ALLOC     = 0x2
SHF_EXECINSTR = 0x4

# st_info 分解
STT_NOTYPE  = 0
STT_OBJECT  = 1
STT_FUNC    = 2
STT_SECTION = 3
STT_FILE    = 4

STT_NAMES = {
    STT_NOTYPE:  "NOTYPE",
    STT_OBJECT:  "OBJECT",
    STT_FUNC:    "FUNC",
    STT_SECTION: "SECTION",
    STT_FILE:    "FILE",
}

STB_LOCAL  = 0
STB_GLOBAL = 1
STB_WEAK   = 2

STB_NAMES = {
    STB_LOCAL:  "LOCAL",
    STB_GLOBAL: "GLOBAL",
    STB_WEAK:   "WEAK",
}

# 特殊节区索引
SHN_UNDEF  = 0
SHN_ABS    = 0xFFF1
SHN_COMMON = 0xFFF2


# ──────────────────────────────────────────────────────────────
# 数据类 —— ELF 解析结果
# ──────────────────────────────────────────────────────────────

@dataclass
class ElfSymbol:
    """符号表条目。"""
    name: str
    address: int
    size: int
    binding: str
    sym_type: str
    section: str
    section_idx: int


@dataclass
class ElfSection:
    """节区头信息。"""
    index: int
    name: str
    name_idx: int
    type_name: str
    type_num: int
    address: int
    offset: int
    size: int
    flags: int
    link: int
    info: int
    addralign: int
    entsize: int

    @property
    def is_writable(self) -> bool:
        return bool(self.flags & SHF_WRITE)

    @property
    def is_alloc(self) -> bool:
        return bool(self.flags & SHF_ALLOC)

    @property
    def is_exec(self) -> bool:
        return bool(self.flags & SHF_EXECINSTR)


@dataclass
class ElfHeader:
    """ELf 头信息。"""
    magic: str
    is_64bit: bool
    little_endian: bool
    os_abi: str
    type_name: str
    machine: str
    entry_point: int
    shoff: int
    shnum: int
    shstrndx: int


@dataclass
class ElfParseResult:
    """ELF 文件解析的完整结果。"""
    header: ElfHeader
    sections: list[ElfSection]
    symbols: list[ElfSymbol]
    files: list[str]
    section_data: dict[str, bytes] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────
# 二进制读取器
# ──────────────────────────────────────────────────────────────

class _BinaryReader:
    """从 bytes 中按指定端序读取各种类型。"""

    def __init__(self, data: bytes, little_endian: bool = True):
        self._data = data
        self._le = little_endian
        self._off = 0

    @property
    def offset(self) -> int:
        return self._off

    def seek(self, pos: int):
        self._off = pos

    def skip(self, n: int):
        self._off += n

    def tell(self) -> int:
        return self._off

    def read(self, n: int) -> bytes:
        result = self._data[self._off:self._off + n]
        self._off += n
        return result

    def u8(self) -> int:
        v = self._data[self._off]
        self._off += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from('<H' if self._le else '>H', self._data, self._off)[0]
        self._off += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from('<I' if self._le else '>I', self._data, self._off)[0]
        self._off += 4
        return v

    def u64(self) -> int:
        v = struct.unpack_from('<Q' if self._le else '>Q', self._data, self._off)[0]
        self._off += 8
        return v

    def str(self, n: int) -> str:
        s = self._data[self._off:self._off + n].decode('ascii', errors='replace')
        self._off += n
        return s

    def cstr_at(self, pos: int) -> str:
        end = self._data.find(b'\x00', pos)
        if end < 0:
            return self._data[pos:].decode('ascii', errors='replace')
        return self._data[pos:end].decode('ascii', errors='replace')


# ──────────────────────────────────────────────────────────────
# ELF 解析器
# ──────────────────────────────────────────────────────────────

class ELFParser:
    """从字节数据解析 ELF 文件。"""

    def __init__(self, data: bytes):
        self._data = data
        self._r = _BinaryReader(data, little_endian=True)
        self._is_64 = False
        self._le = True
        self._shoff = 0
        self._shnum = 0
        self._shentsize = 0
        self._shstrndx = 0
        self._sections: list[ElfSection] = []

    @classmethod
    def from_file(cls, path: str | Path) -> "ELFParser":
        with open(path, 'rb') as f:
            data = f.read()
        return cls(data)

    def parse(self) -> ElfParseResult:
        header = self._parse_header()
        sections = self._parse_sections()
        self._load_shstrtab()
        symbols = self._parse_symbols()
        files = self._parse_line_table()
        section_data = self._collect_section_data()
        return ElfParseResult(
            header=header,
            sections=sections,
            symbols=symbols,
            files=files,
            section_data=section_data,
        )

    # ── Header ────────────────────────────────────────────────

    def _parse_header(self) -> ElfHeader:
        r = self._r
        r.seek(0)

        magic = r.str(4)
        if magic != '\x7fELF':
            raise ValueError(f"Not a valid ELF file: magic={magic!r}")

        elf_class = r.u8()
        if elf_class not in (ELFCLASS32, ELFCLASS64):
            raise ValueError(f"Unsupported ELF class: {elf_class}")
        self._is_64 = (elf_class == ELFCLASS64)

        data_enc = r.u8()
        self._le = (data_enc == ELFDATA2LSB)
        r._le = self._le

        r.u8()  # version
        os_abi_byte = r.u8()
        r.u8()  # abi version
        r.skip(7)  # padding

        os_abi = {
            0x00: "UNIX System V",
            0x41: "ARM EABI",
        }.get(os_abi_byte, f"Unknown(0x{os_abi_byte:02x})")

        e_type = r.u16()
        e_machine = r.u16()
        r.u32()  # e_version

        if self._is_64:
            e_entry = r.u64()
            e_phoff = r.u64()
            self._shoff = r.u64()
        else:
            e_entry = r.u32()
            e_phoff = r.u32()
            self._shoff = r.u32()

        r.u32()  # e_flags
        r.u16()  # e_ehsize
        r.u16()  # e_phentsize
        r.u16()  # e_phnum
        self._shentsize = r.u16()
        self._shnum = r.u16()
        self._shstrndx = r.u16()

        return ElfHeader(
            magic=magic,
            is_64bit=self._is_64,
            little_endian=self._le,
            os_abi=os_abi,
            type_name=ET_NAMES.get(e_type, f"Unknown({e_type})"),
            machine=EM_NAMES.get(e_machine, f"Unknown(0x{e_machine:x})"),
            entry_point=e_entry,
            shoff=self._shoff,
            shnum=self._shnum,
            shstrndx=self._shstrndx,
        )

    # ── Sections ──────────────────────────────────────────────

    def _parse_sections(self) -> list[ElfSection]:
        r = self._r
        sections = []
        r.seek(self._shoff)

        for i in range(self._shnum):
            if self._is_64:
                name_idx = r.u32()
                sec_type = r.u32()
                flags    = r.u64()
                addr     = r.u64()
                offset   = r.u64()
                size     = r.u64()
                link     = r.u32()
                info     = r.u32()
                addralign = r.u64()
                entsize  = r.u64()
            else:
                name_idx = r.u32()
                sec_type = r.u32()
                flags    = r.u32()
                addr     = r.u32()
                offset   = r.u32()
                size     = r.u32()
                link     = r.u32()
                info     = r.u32()
                addralign = r.u32()
                entsize  = r.u32()

            sections.append(ElfSection(
                index=i,
                name='',
                name_idx=name_idx,
                type_name=SHT_NAMES.get(sec_type, f"Unknown(0x{sec_type:x})"),
                type_num=sec_type,
                address=addr,
                offset=offset,
                size=size,
                flags=flags,
                link=link,
                info=info,
                addralign=addralign,
                entsize=entsize,
            ))

        self._sections = sections
        return sections

    def _load_shstrtab(self):
        if self._shstrndx >= len(self._sections):
            return
        sec = self._sections[self._shstrndx]
        if sec.type_num != SHT_STRTAB:
            return

        str_data = self._data[sec.offset:sec.offset + sec.size]
        for s in self._sections:
            end = str_data.find(b'\x00', s.name_idx)
            s.name = (str_data[s.name_idx:end] if end >= 0 else str_data[s.name_idx:]).decode('ascii', errors='replace')

    # ── Symbols ───────────────────────────────────────────────

    def _parse_symbols(self) -> list[ElfSymbol]:
        sym_sec = None
        for s in self._sections:
            if s.type_num == SHT_SYMTAB:
                sym_sec = s
                break
        if sym_sec is None or sym_sec.size == 0:
            return []

        symbols = []
        entry_size = sym_sec.entsize or (24 if self._is_64 else 16)
        str_sec = self._sections[sym_sec.link] if sym_sec.link < len(self._sections) else None
        str_data = self._data[str_sec.offset:str_sec.offset + str_sec.size] if str_sec else b''

        r = self._r
        r.seek(sym_sec.offset)
        count = sym_sec.size // entry_size

        for _ in range(count):
            if self._is_64:
                name_idx = r.u32()
                info     = r.u8()
                other    = r.u8()
                shndx    = r.u16()
                value    = r.u64()
                sz       = r.u64()
            else:
                name_idx = r.u32()
                value    = r.u32()
                sz       = r.u32()
                info     = r.u8()
                other    = r.u8()
                shndx    = r.u16()

            st_type = info & 0xf
            binding = info >> 4

            if name_idx > 0:
                end = str_data.find(b'\x00', name_idx)
                name = str_data[name_idx:end].decode('ascii', errors='replace') if end >= 0 else str_data[name_idx:].decode('ascii', errors='replace')
            else:
                name = ''

            if shndx == SHN_UNDEF:
                sec_name = 'UNDEF'
            elif shndx == SHN_ABS:
                sec_name = 'ABS'
            elif shndx == SHN_COMMON:
                sec_name = 'COMMON'
            elif shndx < len(self._sections):
                sec_name = self._sections[shndx].name
            else:
                sec_name = f'{shndx}'

            symbols.append(ElfSymbol(
                name=name,
                address=value,
                size=sz,
                binding=STB_NAMES.get(binding, f"Unknown({binding})"),
                sym_type=STT_NAMES.get(st_type, f"Unknown({st_type})"),
                section=sec_name,
                section_idx=shndx,
            ))

        return symbols

    # ── DWARF Line Table ──────────────────────────────────────

    def _parse_line_table(self) -> list[str]:
        debug_line_sec = self._find_section('.debug_line')
        if debug_line_sec is None or debug_line_sec.size == 0:
            return []

        buf = self._data
        pos = debug_line_sec.offset
        end = debug_line_sec.offset + debug_line_sec.size
        files: list[str] = []

        while pos < end:
            try:
                r = _BinaryReader(buf, self._le)
                r.seek(pos)

                unit_length = r.u32()
                unit_end = pos + 4 + unit_length
                if unit_end > end:
                    break

                r.u16()  # version
                prologue_length = r.u32()
                r.u8()   # minimum_instruction_length
                r.u8()   # default_is_stmt
                r.u8()   # line_base (signed, skip)
                r.u8()   # line_range
                opcode_base = r.u8()

                # standard_opcode_lengths
                for _ in range(opcode_base - 1):
                    r.u8()

                # Directory Table
                dirs = ['']
                while True:
                    d = self._read_line_cstr(r, buf, unit_end)
                    if d is None:
                        break
                    dirs.append(d)

                # File Name Table
                while True:
                    fname = self._read_line_cstr(r, buf, unit_end)
                    if fname is None:
                        break
                    dir_idx = r.u8()
                    r.u8()  # mtime (simplified ULEB128->single byte)
                    r.u8()  # fsize (simplified)
                    dir_prefix = dirs[dir_idx] if dir_idx < len(dirs) else ''
                    full = f'{dir_prefix}/{fname}'.replace('\\', '/') if dir_prefix else fname
                    files.append(full)

                pos = unit_end
            except Exception:
                break

        return files

    def _read_line_cstr(self, r: _BinaryReader, buf: bytes, unit_end: int) -> Optional[str]:
        start = r.tell()
        if start >= unit_end:
            return None
        end = buf.find(b'\x00', start)
        if end < 0 or end >= unit_end:
            return None
        r.seek(end + 1)
        return buf[start:end].decode('utf-8', errors='replace') if end > start else None

    # ── 辅助 ──────────────────────────────────────────────────

    def _find_section(self, name: str) -> Optional[ElfSection]:
        for s in self._sections:
            if s.name == name:
                return s
        return None

    def _collect_section_data(self) -> dict[str, bytes]:
        result = {}
        for s in self._sections:
            if s.type_num in (SHT_PROGBITS, SHT_NOBITS):
                if s.size > 0 and s.type_num == SHT_PROGBITS:
                    result[s.name] = self._data[s.offset:s.offset + s.size]
                elif s.type_num == SHT_NOBITS:
                    result[s.name] = b'\x00' * s.size
        return result


# ──────────────────────────────────────────────────────────────
# 便捷入口
# ──────────────────────────────────────────────────────────────

def parse_elf(path: str | Path) -> ElfParseResult:
    """解析 ELF 文件，返回完整结果。"""
    return ELFParser.from_file(path).parse()