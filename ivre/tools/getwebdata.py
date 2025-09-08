#! /usr/bin/env python

# This file is part of IVRE.
# Copyright 2011 - 2024 Pierre LALET <pierre@droids-corp.org>
#
# IVRE is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IVRE is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public
# License for more details.
#
# You should have received a copy of the GNU General Public License
# along with IVRE. If not, see <http://www.gnu.org/licenses/>.


"""Fetches IP addresses lists from public websites and creates
data files to add tags to scan results. For now, the following lists are used:

  - CDN & Cloud providers, from
    <https://raw.githubusercontent.com/projectdiscovery/cdncheck/main/sources_data.json>

  - (US) GovCloud IP ranges, from <https://github.com/daehee/govcloud>

  - Tor Exit nodes, from
    <https://check.torproject.org/torbulkexitlist>

  - Scanners operated by the French ANSSI, from
    <https://cert.ssi.gouv.fr/scans/>

  - Scanners operated by the UK NCSC, from
    <https://www.ncsc.gov.uk/information/ncsc-scanning-information>

  - Scanners operated by the Censys, from
    <https://support.censys.io/hc/en-us/articles/360043177092-from-faq>

"""


import argparse
import functools
import json
import os
import re
import socket
from collections.abc import Callable, Generator
from typing import BinaryIO
from urllib.error import HTTPError, URLError

from ivre import config
from ivre.data import govcloud
from ivre.utils import (
    IPADDR,
    LOGGER,
    NETADDR,
    download_if_newer,
    generic_ipaddr_extractor,
    generic_processor,
    make_range_tables,
    net2range,
)


def cdnjson2table(infd: BinaryIO, outfd: BinaryIO) -> None:
    table = make_range_tables(
        net2range(net) + ((ntype, name),)
        for ntype, name_nets in json.load(infd).items()
        for name, nets in name_nets.items()
        for net in nets
        if NETADDR.search(net)  # TODO: handle domain names
    )
    outfd.write(b"[\n    (\n")
    outfd.writelines(f"        {elt[0]!r},\n".encode() for elt in table)
    outfd.write(b"    ),\n    (\n")
    outfd.writelines(f"        {elt[1]!r},\n".encode() for elt in table)
    outfd.write(b"    ),\n]\n")


def censys_net_extractor(fdesc: BinaryIO) -> Generator[str, None, None]:
    page_content = fdesc.read().decode('utf-8')
    block_match = re.search(r"<code[^>]*>(.*?)</code>", page_content, re.DOTALL)
    if not block_match:
        return
    block = block_match.group(1)
    lines = block.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        expr = re.compile(f"{IPADDR.pattern[1:-1]}(/[0-9]+)")
        for m in expr.finditer(line):
            addr, mask = m.groups()
            if mask is None:
                if ":" in addr:
                    yield f"{addr}/128"
                else:
                    yield f"{addr}/32"
            else:
                yield f"{addr}{mask}"


def dns_get_names(name: str) -> list[str]:
    return sorted(
        set(
            ans[4][0]
            for ans in socket.getaddrinfo(name, None)
            if isinstance(ans[4][0], str)
        )
    )


assert config.DATA_PATH is not None
URLS: list[tuple[str, str, Callable[[BinaryIO, BinaryIO], None]]] = [
    (
        "https://raw.githubusercontent.com/projectdiscovery/cdncheck/main/sources_data.json",
        os.path.join(config.DATA_PATH, "cdn_nuclei.py"),
        cdnjson2table,
    ),
    (
        "https://check.torproject.org/torbulkexitlist",
        os.path.join(config.DATA_PATH, "tor_exit_nodes.txt"),
        functools.partial(generic_processor, generic_ipaddr_extractor),
    ),
    (
        "https://cert.ssi.gouv.fr/scans/",
        os.path.join(config.DATA_PATH, "ssigouvfr_scanners.txt"),
        functools.partial(generic_processor, generic_ipaddr_extractor),
    ),
    (
        "https://docs.censys.com/docs/opt-out-of-data-collection",
        os.path.join(config.DATA_PATH, "censys_scanners.txt"),
        functools.partial(generic_processor, censys_net_extractor),
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    for url, fname, processor in URLS:
        try:
            download_if_newer(url, fname, processor=processor)
        except Exception:
            pass
    assert config.DATA_PATH is not None
    with open(
        os.path.join(config.DATA_PATH, "ukncsc_scanners.txt"), "w", encoding="utf8"
    ) as fdesc:
        fdesc.writelines(
            f"{addr}\n"
            for addr in dns_get_names("scanner.scanning.service.ncsc.gov.uk")
        )
    try:
        govcloud.fetch_and_build()
    except HTTPError as exc:
        LOGGER.error("Cannot download govcloud data [%s]", exc)
    except URLError as exc:
        LOGGER.error("Cannot download govcloud data [%s]", exc)
