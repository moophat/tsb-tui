"""Recognize foreign export containers for the fixture-server importer."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.parsers import expat

from . import _import_yaml


class UnsupportedFormat(ValueError):
    """The bytes do not identify the declared export container."""


def read(data: bytes, format_name: str):
    if format_name == "yaml":
        value = _import_yaml.load(data)
        return value if isinstance(value, list) else [value]
    if format_name == "tabby":
        value = _import_yaml.load(data)
        if not isinstance(value, dict) or value.get("version") not in (1, 2) or not isinstance(value.get("profiles"), list):
            raise UnsupportedFormat("not a supported Tabby export")
        rows = []
        groups = {group.get("id"): group for group in value.get("groups", []) if isinstance(group, dict)}
        for profile in value["profiles"]:
            if not isinstance(profile, dict):
                rows.append(profile)
                continue
            options = profile.get("options", {})
            row = {key: profile[key] for key in ("id", "type", "name", "group", "options") if key in profile}
            if isinstance(options, dict):
                options = dict(options)
                if "password" in options:
                    row["_password"] = options.pop("password")
                if "privateKeys" in options:
                    keys = options.pop("privateKeys")
                    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
                        raise ValueError("invalid key references")
                    row["_key_file"] = any(not key.startswith("vault://") for key in keys)
                    row["_unreadable_key"] = any(key.startswith("vault://") for key in keys)
                row["options"] = options
            group = row.get("group")
            if group in groups:
                segments, seen = [], set()
                while group in groups:
                    if group in seen:
                        raise ValueError("group cycle")
                    seen.add(group)
                    item = groups[group]
                    segments.append(item.get("name", ""))
                    group = item.get("parentGroupId")
                row["group"] = "/".join(reversed(segments))
            rows.append(row)
        return rows
    if format_name == "securecrt":
        # Only a UTF-8 export is supported (optional BOM); a UTF-16 file bypasses the ASCII byte search,
        # so refuse any bytes that do not decode as UTF-8 before parsing. Then disable DTD/entity
        # processing with expat handlers, so an internal DTD is rejected however the file is encoded.
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UnsupportedFormat("not a supported XML export")

        def forbid(*_args):
            raise ValueError("XML entities are forbidden")

        parser = expat.ParserCreate()
        parser.StartDoctypeDeclHandler = forbid
        parser.EntityDeclHandler = forbid
        try:
            parser.Parse(text.encode("utf-8"), True)
        except expat.ExpatError as exc:
            raise UnsupportedFormat("not a supported XML export") from exc
        root = ET.fromstring(text)
        if root.tag != "VanDyke" or root.get("version") != "3.0":
            raise UnsupportedFormat("not a supported XML export")
        sessions = next((child for child in root if child.tag == "key" and child.get("name") == "Sessions"), None)
        if sessions is None:
            raise UnsupportedFormat("no sessions")
        rows = []

        def visit(node, parents):
            name = node.get("name", "")
            path = parents + [name]
            fields = {child.get("name"): child.text or "" for child in node if child.tag in ("string", "dword")}
            if fields.get("Is Session") == "1" and name != "Default":
                protocol = fields.get("Protocol Name")
                kind = {"SSH2": "ssh", "Telnet": "telnet", "Serial": "serial"}.get(protocol, "unsupported")
                row = {"id": "/".join(path), "name": name, "type": kind,
                       "options": {"host": fields.get("Hostname", "")}}
                # Password/Password V2 are credential fields only: never copied into the target, and
                # unreadable in the mock (no isolated decoder), so the row is retained and reported.
                if fields.get("Password V2") or fields.get("Password"):
                    row["_unreadable_key"] = True
                if fields.get("Identity Filename") or fields.get("Identity Filename V2"):
                    row["_key_file"] = True
                if parents:
                    row["group"] = "/".join(parents)
                report, position, ports = [], 0, []
                for child in node:
                    if child.tag not in ("string", "dword"):
                        continue
                    pname, val = child.get("name"), child.text or ""
                    # Structural and credential/key properties are not target options.
                    if pname in ("Is Session", "Protocol Name", "Password", "Password V2",
                                 "Identity Filename", "Identity Filename V2"):
                        continue
                    if pname == "Credential Title":
                        row["_unreadable_key"] = True
                        continue
                    position += 1
                    if pname == "Hostname":
                        row["options"]["host"] = val
                    elif pname == "Username" and kind == "ssh" and val:
                        row["options"]["user"] = val
                    elif pname in ("[SSH2] Port", "Port", "[Telnet] Port") and val:
                        # Collect every applicable alias; resolve once, after the session's properties.
                        ports.append(val)
                    elif kind == "ssh" and pname == "Forward X11":
                        row["options"]["x11"] = val == "1"
                    elif kind == "ssh" and pname in ("Forward Agent", "Enable OpenSSH Agent Forwarding"):
                        row["options"]["agentForward"] = val == "1"
                    elif kind == "ssh" and pname == "Idle NO-OP Timeout" and fields.get("Idle NO-OP Check") == "1":
                        if re.fullmatch(r"[0-9]+", val):
                            row["options"]["keepaliveInterval"] = int(val)
                        else:
                            # A malformed numeric property is this row's failure, never the export's.
                            row["_invalid_row"] = True
                    else:
                        report.append((None, position))
                if ports:
                    if not all(re.fullmatch(r"[0-9]+", v) for v in ports):
                        row["_invalid_row"] = True
                    elif len({int(v) for v in ports}) > 1:
                        # Alias properties that coexist inconsistently have no winner; the row is
                        # reported, not silently resolved by document order (mapping table).
                        row["_invalid_row"] = True
                    else:
                        row["options"]["port"] = int(ports[0])
                if report:
                    row["_options_report"] = report
                rows.append(row)
            for child in node:
                if child.tag == "key":
                    visit(child, path)

        for child in sessions:
            if child.tag == "key":
                visit(child, [])
        return rows
    if format_name == "mobaxterm":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
        sections = []
        current = None
        has_credentials = False
        for line in text.splitlines():
            if line.startswith("[") and line.endswith("]"):
                name = line[1:-1]
                if name in ("Passwords", "Credentials"):
                    # Saved-password sections are stripped; the bookmarks that use them stay parseable
                    # and are reported as unreadable credentials (the mock has no decoder).
                    has_credentials = True
                    current = None
                    continue
                current = {"entries": []} if re.fullmatch(r"Bookmarks(?:_[0-9]+)?", name) else None
                if current is not None:
                    sections.append(current)
            elif current is not None and "=" in line:
                key, value = line.split("=", 1)
                if key in ("SubRep", "ImgNum"):
                    if key in current:
                        raise ValueError("duplicate bookmark metadata")
                    current[key] = value
                else:
                    current["entries"].append((key.rstrip(), value))
        if not sections or any("SubRep" not in section or "ImgNum" not in section for section in sections):
            raise UnsupportedFormat("not a bookmark export")
        rows = []
        for section in sections:
            group = section["SubRep"].replace("\\", "/")
            for name, value in section["entries"]:
                parts = value.split("#")
                if len(parts) < 3 or not parts[1].isdigit():
                    raise ValueError("invalid bookmark")
                fields = parts[2].split("%")
                kind = {"0": "ssh", "1": "telnet", "8": "serial"}.get(fields[0], "unsupported")
                row = {"id": "/".join(filter(None, (group, name))), "name": name, "type": kind,
                       "options": {"host": fields[1] if len(fields) > 1 else ""}}
                if any(len(fields) > i and fields[i] for i in (14, 15)):
                    row["_key_file"] = True
                if group:
                    row["group"] = group
                if len(fields) > 2 and fields[2]:
                    if re.fullmatch(r"[0-9]+", fields[2]):
                        row["options"]["port"] = int(fields[2])
                    else:
                        # A malformed port is this bookmark's invalid row, never the export's.
                        row["_invalid_row"] = True
                if kind == "ssh" and len(fields) > 3 and fields[3]:
                    row["options"]["user"] = fields[3]
                rows.append(row)
        if has_credentials:
            for row in rows:
                row["_unreadable_key"] = True
        return rows
    raise UnsupportedFormat("unknown format")
