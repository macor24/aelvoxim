"""TLS 信任链版本闸（GHSA-6q48-6c7w-9gw2，2026-11-03）

certifi < 2026.10.19 仍带 `emSign ECC Root CA - G3`，其下级 8 张中间证书私钥已泄露，
可被用来签发任意域名的"合法"证书（on-path 即可 MITM）。修法：certifi >= 2026.10.19
（2026.10.18 会把未受影响的 emSign 根也删掉，别停在 .18）。

这条测试同时锁住两件事：装的是修过的版本；bundle 里确实没有那张根。
"""
import re

import certifi


def _ver(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def test_certifi_version_is_patched():
    assert _ver(certifi.__version__) >= _ver("2026.10.19"), (
        f"certifi {certifi.__version__} 仍受影响（GHSA-6q48-6c7w-9gw2）"
    )


def test_compromised_emsign_root_absent():
    bundle = open(certifi.where(), encoding="utf-8", errors="replace").read()
    assert "emSign ECC Root CA - G3" not in bundle, "被泄露的 emSign G3 根仍在信任库里"


def test_bundle_still_has_a_usable_store():
    """删根不能把库删空：证书数应仍在百张量级。"""
    bundle = open(certifi.where(), encoding="utf-8", errors="replace").read()
    assert bundle.count("BEGIN CERTIFICATE") >= 100
