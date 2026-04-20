"""Host plugin registry and metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Type

from .base import BaseHostPlugin
from .buzzheavier import BuzzheavierPlugin
from .catbox import CatboxPlugin
from .dummy_local import DummyLocalPlugin
from .fileio import FileIoPlugin
from .gofile import GofilePlugin
from .onefichier import OneFichierPlugin
from .send_now import SendNowPlugin
from .tmpfiles_org import TmpfilesOrgPlugin
from .transfer_sh import TransferShPlugin
from .upload_ee import UploadEePlugin
from .vikingfile import VikingfilePlugin


PROFILE_CAPABILITIES = {
    "size_10gb_plus": {
        "1fichier.com",
        "bowfile.com",
        "gofile.io",
        "hitfile.net",
        "buzzheavier.com",
        "vikingfile.com",
        "dailyuploads.net",
        "send.now",
        "uploadhive.com",
        "dfiles.eu",
        "download.gg",
        "anontransfer.com",
        "filemirage.com",
        "filebin.net",
        "bashupload.com",
        "desiupload.co",
        "udrop.com",
        "fast-down.com",
    },
    "retention_60d_plus": {
        "mexa.sh",
        "megaup.net",
        "dfiles.eu",
        "download.gg",
        "sendvid.com",
        "filemirage.com",
        "gofile.to",
        "lain.la",
        "filer.net",
        "media.cm",
        "qu.ax",
        "ibb.co",
        "end2end.tech",
        "hostuje.net",
        "m1r.ai",
        "xup.in",
        "dropmb.com",
        "atomauth.com",
    },
    "retention_lt_1d": {
        "litter.catbox.moe",
        "tmpfiles.org",
        "uguu.se",
        "lurkmore.com",
        "aishiteiru.moe",
        "tempfiles.ninja",
        "filetmp.com",
    },
    "manual_delete": {
        "1fichier.com",
        "bowfile.com",
        "gofile.io",
        "1cloudfile.com",
        "upload.ee",
        "mexa.sh",
        "filespace.com",
        "gulf-up.com",
        "uploadhive.com",
        "download.gg",
        "uploady.io",
        "filebin.net",
        "douploads.net",
        "dataupload.net",
        "rapidshare.io",
        "desiupload.co",
        "tempfiles.ninja",
        "udrop.com",
        "dosya.co",
        "uploadfile.pl",
        "filestore.to",
        "end2end.tech",
        "1filesharing.com",
        "mega4upload.net",
        "hostuje.net",
        "dz4up.com",
        "wdfiles.ru",
        "xup.in",
        "filepv.com",
        "ayaya.beauty",
        "nelion.me",
    },
}

CAPABILITY_LABELS = {
    "size_10gb_plus": "10GB+",
    "retention_60d_plus": "60d+",
    "retention_lt_1d": "<1d",
    "manual_delete": "delete",
}


@dataclass(frozen=True)
class PluginDescriptor:
    key: str
    cls: Type[BaseHostPlugin]
    domain: str

    @property
    def capability_tags(self) -> List[str]:
        tags: List[str] = []
        for cap_key, hosts in PROFILE_CAPABILITIES.items():
            if self.domain in hosts:
                tags.append(CAPABILITY_LABELS[cap_key])
        return tags


PLUGIN_REGISTRY: Dict[str, PluginDescriptor] = {
    TransferShPlugin.host_key: PluginDescriptor(
        key=TransferShPlugin.host_key,
        cls=TransferShPlugin,
        domain=TransferShPlugin.domain,
    ),
    FileIoPlugin.host_key: PluginDescriptor(
        key=FileIoPlugin.host_key,
        cls=FileIoPlugin,
        domain=FileIoPlugin.domain,
    ),
    CatboxPlugin.host_key: PluginDescriptor(
        key=CatboxPlugin.host_key,
        cls=CatboxPlugin,
        domain=CatboxPlugin.domain,
    ),
    TmpfilesOrgPlugin.host_key: PluginDescriptor(
        key=TmpfilesOrgPlugin.host_key,
        cls=TmpfilesOrgPlugin,
        domain=TmpfilesOrgPlugin.domain,
    ),
    BuzzheavierPlugin.host_key: PluginDescriptor(
        key=BuzzheavierPlugin.host_key,
        cls=BuzzheavierPlugin,
        domain=BuzzheavierPlugin.domain,
    ),
    OneFichierPlugin.host_key: PluginDescriptor(
        key=OneFichierPlugin.host_key,
        cls=OneFichierPlugin,
        domain=OneFichierPlugin.domain,
    ),
    GofilePlugin.host_key: PluginDescriptor(
        key=GofilePlugin.host_key,
        cls=GofilePlugin,
        domain=GofilePlugin.domain,
    ),
    SendNowPlugin.host_key: PluginDescriptor(
        key=SendNowPlugin.host_key,
        cls=SendNowPlugin,
        domain=SendNowPlugin.domain,
    ),
    UploadEePlugin.host_key: PluginDescriptor(
        key=UploadEePlugin.host_key,
        cls=UploadEePlugin,
        domain=UploadEePlugin.domain,
    ),
    VikingfilePlugin.host_key: PluginDescriptor(
        key=VikingfilePlugin.host_key,
        cls=VikingfilePlugin,
        domain=VikingfilePlugin.domain,
    ),
    DummyLocalPlugin.host_key: PluginDescriptor(
        key=DummyLocalPlugin.host_key,
        cls=DummyLocalPlugin,
        domain=DummyLocalPlugin.domain,
    ),
}


def get_plugin_registry() -> Dict[str, PluginDescriptor]:
    return PLUGIN_REGISTRY
