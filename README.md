# 机械革命无界14+ EC 逆向分析与工具集

> 适用机型: **MECHREVO 无界14+ (ARN39P-TF, AMD Ryzen 7 7840HS, Phoenix/FP7)**
> 固件版本: BIOS 2.31 / EC 2.12 (其他版本寄存器布局可能不同)
> 分析环境: Linux + [opencode](https://opencode.ai) + GLM-5.3

对机械革命无界14+的32MB SPI固件(BIOS + ITE EC)做了完整逆向,
把固件里**预留但从未对用户开放的功能**全部挖了出来, 并做成可直接使用的Linux工具。

## 功能一览

| 功能 | 状态 | 说明 |
|------|------|------|
| 🎹 **Fn功能锁** | ✅ 一次写入永久生效 | F1-F12直接输出媒体键(音量/亮度), 不用再按Fn+Fx |
| 🌀 **现代风扇曲线** | ✅ 需守护进程 | 低温停转全静音 / 连续插值平滑调速 / 满血散热上限 |
| 💡 **键盘背光常亮** | ✅ 需守护进程 | 默认30秒无操作自动熄灭 → 常亮, F9调档不受影响 |
| 🎚 手动风扇转速 | ✅ 即时命令 | 0-184任意值, 用于测试/压温度 |
| 📊 EC状态读取 | ✅ | 温度/转速/电池/性能模式/EC版本 |
| 🔋 电池充电限制 | ❌ 固件未实现 | EC里是死代码, 详见分析报告(7组实验完整证伪) |
| 🔧 ROM风扇表补丁 | ⚠️ 需编程器 | 永久修改原厂风扇曲线的工具(附原值校验) |

## 关键硬件发现

- **风扇PWM有效范围是 0-184, 不是 0-255** — 超过184风扇**直接停转**!
  (PWM周期寄存器0xB8, 实测PWM 200/220/255全部RPM=0。EC固件自身斜坡也钳在184)
- 风扇转速响应: PWM 40→1727rpm, 184→5719rpm (全程线性, ~27.7 RPM/单位)
- 原厂满载只给到PWM 173(94%), "压不住温度"是散热模组物理到顶而非软件限制
- EC通过ITE I2EC协议(端口0x4E/0x4F)访问全部XRAM, 但**只能到0x7FFF**,
  0x9600(EC温度源)等越界地址不可达
- Fn锁/性能模式/背光超时等状态通过"IP3"配置块**自动持久化到flash**

## 快速开始

所有工具需要 root (访问 /dev/mem 和 /dev/port):

```bash
git clone https://github.com/viewv/mechrevo-wujie14-ec.git
cd mechrevo-wujie14-ec
```

### 1. Fn功能锁 (最简单, 写一次永久生效)

```bash
sudo python3 tools/ec_power_fn.py fnlock on     # F1-F12 → 直接媒体键
sudo python3 tools/ec_power_fn.py fnlock off    # 恢复
```

原理: EC固件预留的FNLK寄存器(XRAM 0x41A) + 原生持久化机制,
详见[逆向分析报告](docs/逆向分析报告.md)第八章。重启依然生效。

### 2. 风扇曲线守护 (静音待机 + 平滑调速)

```bash
# 先试运行 (Ctrl+C退出自动恢复EC原厂控制)
sudo python3 daemons/wujie_fand.py run

# 满意后装成服务
sudo cp daemons/wujie_fand.py /usr/local/bin/
sudo cp daemons/wujie_fand.json /etc/
sudo cp daemons/wujie-fand.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now wujie-fand
journalctl -u wujie-fand -f    # 看实时调温
```

默认曲线 (在 `/etc/wujie_fand.json` 里改, 改完 `systemctl restart wujie-fand`):

| 温度 | PWM | 转速约 | 感受 |
|------|-----|--------|------|
| <50°C | 0 | 停转 | 完全静音 |
| 54°C重启 | 45-50 | ~2000rpm | 刚可闻 |
| 60°C | 60 | ~2500rpm | 轻载区 |
| 72°C | 115 | ~4400rpm | 重载 |
| 91°C+ | 184 | ~5720rpm | 硬件极限 |

### 3. 键盘背光常亮

```bash
sudo python3 daemons/wujie_kblight.py run       # 试运行

sudo cp daemons/wujie_kblight.py /usr/local/bin/
sudo cp daemons/wujie-kblight.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now wujie-kblight
```

原理: EC背光超时倒数寄存器(XRAM 0x3E1)的 0xFF=禁用倒数 是固件预留的常亮值,
但任何键盘按键都会刷回30秒(KBTT=0默认), 所以需要守护进程持续压制。

### 4. 手动/测试工具

```bash
sudo python3 tools/ec_fan_control.py status     # 风扇全景状态
sudo python3 tools/ec_fan_control.py set 120    # 双风扇手动PWM (0-184!)
sudo python3 tools/ec_fan_control.py sweep      # PWM-RPM响应扫描
sudo python3 tools/ec_fan_control.py auto       # 恢复EC自动控制

sudo python3 tools/ec_power_fn.py status        # 电源/Fn/电池全景
sudo python3 tools/ec_power_fn.py setmode 2     # 性能模式0-3
sudo python3 tools/ec_power_fn.py battery       # 电池详情
```

## 目录结构

```
├── docs/
│   ├── 逆向分析报告.md        # 完整逆向过程: BIOS/EC解包、寄存器地图、
│   │                          # 风扇曲线解码、充电限制实验记录、硬件边界验证
│   ├── 风扇表原始数据.txt     # 原厂风扇曲线表ROM dump
│   ├── DSDT_ARN39P.dsl        # 反编译的DSDT (EC ACPI接口)
│   └── bios-options/          # BIOS Setup选项IFR提取 (含隐藏的CBS风扇选项)
├── tools/                     # 交互式工具
│   ├── ec_power_fn.py         # Fn锁/性能模式/电池/EC寄存器读写
│   ├── ec_fan_control.py      # 风扇手动控制/扫描/寄存器分析
│   ├── patch_fan_tables.py    # ROM风扇表补丁器(需编程器刷写)
│   └── ec_probe.sh            # EC寄存器探测实验脚本
└── daemons/                   # 常驻守护进程
    ├── wujie_fand.py + .json + .service     # 风扇曲线守护
    ├── wujie_kblight.py + .service          # 背光常亮守护
    └── fnlock-boot.service                  # Fn锁开机保障(可选, 通常不需要)
```

## 兼容性说明

- **本仓库针对 无界14+ (ARN39P)**, 寄存器布局逆向自 EC固件V2.12
- 同代同方(Tongfang)准系统机器(无界14/16, IP3TECH EC家族)大概率部分通用,
  使用前先用 `ec_power_fn.py status` 验证读数是否合理
- 新一代无界14X (T142-STX) 寄存器布局不同(如手动风扇在0x0741), 不要直接套用
- **本仓库不包含原厂ROM** (版权原因), 需要刷写场景请自行从官方获取或编程器备份

## ⚠️ 免责声明

本项目所有工具直接读写嵌入式控制器(EC)寄存器, 属于**非官方黑盒逆向产物**:

- **仅供学习研究**, 由此对您的笔记本造成的任何损坏(过热/不开机/数据丢失等)
  **作者不承担任何责任**, 风险自负
- EC控制失效保护: 所有守护进程崩溃/停止时自动恢复EC原厂控制逻辑,
  但仍建议重要数据做好备份
- 风扇PWM超过184会导致风扇停转(散热失效), 工具已内置钳位, 请勿修改后滥用
- 刷写修改过的ROM需要编程器救砖能力, 没有把握请不要尝试

## 分析方法

- 固件解包: UEFIExtract (AMI AptioV结构) + 自制8051反汇编器
  (逆向ITE EC的8051代码定位所有控制逻辑)
- 验证方式: DSDT反编译交叉验证 + I2EC实时读写测试 + PWM-RPM扫描
- **全部分析由 [opencode](https://opencode.ai) + GLM-5.3 (AI) 完成**,
  人工仅负责执行测试与验证结论

## 相关项目

- [jpy794/wujie-fan-control](https://github.com/jpy794/wujie-fan-control) — 无界16Pro,
  I2EC协议参考来源
- [Bei-Luo/wujie-fan-control](https://github.com/Bei-Luo/wujie-fan-control) — 无界14X,
  确认了同家族寄存器

## License

MIT — 见 [LICENSE](LICENSE)
