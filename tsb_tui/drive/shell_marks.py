"""Incremental OSC 133 state from live output, independent of grid cell tags."""
from __future__ import annotations


class ShellMarks:
    def __init__(self) -> None:
        self.prompt_seen = False
        self._at_prompt = True
        self.last_mark: str | None = None
        self.exit_serial = 0
        self.exit_code: int | None = None
        self._escape = False
        self._osc = False
        self._prefix = b''
        self._mark: str | None = None
        self._code: int | None = None
        self._digits = False
        self._digit_count = 0
        self._field = 'mark'
        self._valid = False

    @property
    def at_prompt(self) -> bool:
        return not self.prompt_seen or self._at_prompt

    def feed(self, data: bytes) -> None:
        if not self._osc and not self._escape and data.find(b'\x1b') < 0:
            return
        for byte in data:
            if self._osc:
                if byte == 7 or (self._escape and byte == 92):
                    self._finish()
                    self._osc = self._escape = False
                    continue
                if self._escape:
                    self._valid = False
                self._escape = byte == 27
                if self._escape:
                    continue
                if not self._valid:
                    continue
                if len(self._prefix) < 4:
                    self._prefix += bytes((byte,))
                    self._valid = b'133;'.startswith(self._prefix)
                elif self._mark is None:
                    self._mark = chr(byte)
                    self._valid = self._mark in ('A', 'B', 'C', 'D')
                elif self._field == 'options':
                    continue
                elif self._field == 'mark':
                    if byte == 59:
                        self._field = 'code' if self._mark == 'D' else 'options'
                        self._code = 0
                        self._digit_count = 0
                    else:
                        self._valid = False
                elif byte == 59:
                    self._field = 'options'
                elif self._field == 'code' and 48 <= byte <= 57:
                    self._digit_count += 1
                    if (self._digit_count > 10
                            or self._code * 10 + byte - 48 > 2147483647):
                        self._field = 'invalid_code'
                        self._digits = False
                    else:
                        self._digits = True
                        self._code = self._code * 10 + byte - 48
                else:
                    self._field = 'invalid_code'
                    self._digits = False
            elif self._escape and byte == 93:
                self._osc = self._valid = True
                self._escape = False
                self._prefix = b''
                self._mark = self._code = None
                self._digits = False
                self._digit_count = 0
                self._field = 'mark'
            else:
                self._escape = byte == 27

    def _finish(self) -> None:
        if not self._valid or self._mark is None:
            return
        self.last_mark = self._mark
        if self._mark == 'A':
            self.prompt_seen = True
            self._at_prompt = True
        elif self._mark in ('C', 'D'):
            self._at_prompt = False
        if self._mark == 'D':
            self.exit_serial += 1
            self.exit_code = self._code if self._digits else None
