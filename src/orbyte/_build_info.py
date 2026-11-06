"""构建期注入的常量（由 build.ps1 覆盖生成，平时保留默认值即可）。

为什么单独一个文件：服务器地址和版本号要能"编译进去"，
但源码里不能出现真实域名——源码是要进 git 的，地址换了不该改代码。
build.ps1 打包前会用实际值重写本文件，打完再还原。
"""

from __future__ import annotations

#: 编译时注入的默认服务器地址（空 = 未注入，运行时由用户手填）
BUILD_SERVER_URL = ""

#: 编译时注入的版本号（空 = 用 orbyte.__version__）
BUILD_VERSION = ""
