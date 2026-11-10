#!/usr/bin/env python3
"""
无界14+ (ARN39P) ROM风扇表修改器 (方案B: 永久修改)

修改 ROM 中 0x2ECB / 0x2F25 两处风扇曲线表, 生成新ROM用于刷写。

表块布局 (以T1起始为基准, 每风扇块共0x5A字节, 逆向自EC固件V2.12):
  +0x00 T1[15]  降温阈值°C (低于此温度降一级; 等级0-8有效)
  +0x0F (1字节, 值54, 保留间隔字节 - 勿动)
  +0x10 T2[14]  升温阈值°C (等级0-7的8个值 + 6个FF填充; FF=封顶不可达)
  +0x1E PWM[15] 各等级占空比 0-255 (等级0-8有效)
  +0x2D RPM[15] 各等级目标转速 ×100 (等级0-8有效)
  +0x3C E[15]   降级防抖周期
  +0x4B F[15]   升级防抖周期

原厂曲线 (Fan1 @ROM 0x2ECB, Fan2 @0x2F25):
  T1(降) : 0,53,58,63,69,75,81,92,97
  T2(升) : 55,60,66,72,78,84,95,99,(FF=封顶)
  PWM F1 : 47,75,86,97,111,127,140,153,173   → 最高67.8%
  PWM F2 : 39,65,73,83,97,120,129,136,150,162 → 最高63.5%
  RPM F1 : 17,27,31,34,38,42,45,48,53 (×100)
  RPM F2 : 14,24,27,30,34,40,42,44,50 (×100)

用法:
  python3 patch_fan_tables.py <原ROM> <新ROM> <模式>
     模式: aggressive  激进散热(拉满转速, 压7840HS满载温度)
           quiet       安静(延迟升温+降转速)
           custom      自定义(编辑本文件 CUSTOM_* 数组)
           restore     还原为原厂表
"""
import sys

# 表在ROM中的绝对偏移 (T1表起始)
FAN1_BLOCK = 0x2ECB
FAN2_BLOCK = 0x2F25

# 块内偏移
OFF_T1 = 0x00     # 15字节
OFF_GAP = 0x0F   # 1字节间隔(值54), 保留
OFF_T2 = 0x10     # 14字节 (8值+6×FF)
OFF_PWM = 0x1E    # 15字节
OFF_RPM = 0x2D    # 15字节
OFF_E = 0x3C      # 15字节
OFF_F = 0x4B      # 15字节

# ============ 原厂值 ============
ORIG_T1 = [0, 53, 58, 63, 69, 75, 81, 92, 97, 97, 97, 97, 97, 97, 97]
ORIG_T2 = [55, 60, 66, 72, 78, 84, 95, 99, 255, 255, 255, 255, 255, 255]  # 14字节
FAN1_ORIG_PWM = [47, 75, 86, 97, 111, 127, 140, 153, 173, 173, 173, 173, 173, 173, 173]
FAN2_ORIG_PWM = [39, 65, 73, 83, 97, 120, 129, 136, 150, 162, 162, 162, 162, 162, 162]
FAN1_ORIG_RPM = [17, 27, 31, 34, 38, 42, 45, 48, 53, 53, 53, 53, 53, 53, 53]
FAN2_ORIG_RPM = [14, 24, 27, 30, 34, 40, 42, 44, 50, 50, 50, 50, 50, 50, 50]
ORIG_E = [1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3]
ORIG_F = [1, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7]

# ============ 预设方案 ============

# aggressive: 更早升温 + 更满转速 (7840HS满载压温度)
AGGR_T1 = [0, 50, 55, 60, 65, 70, 75, 85, 92, 92, 92, 92, 92, 92, 92]
AGGR_T2 = [52, 57, 62, 67, 72, 77, 82, 95, 255, 255, 255, 255, 255, 255]
AGGR_FAN1_PWM = [50, 90, 115, 135, 155, 175, 195, 215, 235, 235, 235, 235, 235, 235, 235]
AGGR_FAN2_PWM = [45, 85, 110, 130, 150, 170, 190, 210, 230, 230, 230, 230, 230, 230, 230]
AGGR_FAN1_RPM = [20, 30, 35, 40, 45, 50, 55, 60, 65, 65, 65, 65, 65, 65, 65]
AGGR_FAN2_RPM = [18, 28, 33, 38, 43, 48, 53, 58, 62, 62, 62, 62, 62, 62, 62]

# quiet: 延迟升温 + 降转速 (牺牲温度换安静)
QUIET_T1 = [0, 55, 60, 66, 72, 78, 84, 95, 99, 99, 99, 99, 99, 99, 99]
QUIET_T2 = [58, 64, 70, 77, 84, 90, 99, 105, 255, 255, 255, 255, 255, 255]
QUIET_FAN1_PWM = [40, 55, 65, 75, 90, 105, 120, 140, 160, 160, 160, 160, 160, 160, 160]
QUIET_FAN2_PWM = [35, 50, 60, 70, 85, 100, 115, 135, 155, 155, 155, 155, 155, 155, 155]
QUIET_FAN1_RPM = FAN1_ORIG_RPM
QUIET_FAN2_RPM = FAN2_ORIG_RPM

# custom: 在这里填你想要的值 (数组长度必须=15, T2=14)
CUSTOM_T1 = AGGR_T1
CUSTOM_T2 = AGGR_T2
CUSTOM_FAN1_PWM = AGGR_FAN1_PWM
CUSTOM_FAN2_PWM = AGGR_FAN2_PWM
CUSTOM_FAN1_RPM = AGGR_FAN1_RPM
CUSTOM_FAN2_RPM = AGGR_FAN2_RPM
CUSTOM_E = ORIG_E
CUSTOM_F = ORIG_F


def write_block(rom, base, t1, t2, pwm, rpm, e, f):
    assert len(t1) == 15, "T1必须15字节"
    assert len(t2) == 14, "T2必须14字节"
    assert len(pwm) == 15, "PWM必须15字节"
    assert len(rpm) == 15, "RPM必须15字节"
    assert len(e) == 15 and len(f) == 15
    # 校验不越界(等级0-8是活跃区, 但写满全表保持结构一致)
    rom[base + OFF_T1:base + OFF_T1 + 15] = bytes(t1)
    rom[base + OFF_T2:base + OFF_T2 + 14] = bytes(t2)
    rom[base + OFF_PWM:base + OFF_PWM + 15] = bytes(pwm)
    rom[base + OFF_RPM:base + OFF_RPM + 15] = bytes(rpm)
    rom[base + OFF_E:base + OFF_E + 15] = bytes(e)
    rom[base + OFF_F:base + OFF_F + 15] = bytes(f)


def verify_orig(rom):
    """验证ROM与预期原厂表一致 (防止刷错固件版本)"""
    checks = [
        (FAN1_BLOCK + OFF_T1, ORIG_T1),
        (FAN1_BLOCK + OFF_T2, ORIG_T2),
        (FAN1_BLOCK + OFF_PWM, FAN1_ORIG_PWM),
        (FAN1_BLOCK + OFF_RPM, FAN1_ORIG_RPM),
        (FAN2_BLOCK + OFF_T1, ORIG_T1),
        (FAN2_BLOCK + OFF_T2, ORIG_T2),
        (FAN2_BLOCK + OFF_PWM, FAN2_ORIG_PWM),
        (FAN2_BLOCK + OFF_RPM, FAN2_ORIG_RPM),
    ]
    bad = 0
    for off, arr in checks:
        actual = list(rom[off:off + len(arr)])
        if actual != arr:
            print(f"[!] 校验失败 @0x{off:05X}: 期望{arr} 实际{actual}")
            bad += 1
    return bad == 0


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    src, dst, mode = sys.argv[1], sys.argv[2], sys.argv[3]

    rom = bytearray(open(src, 'rb').read())
    if len(rom) != 32 * 1024 * 1024:
        print(f"[!] 警告: ROM大小 {len(rom)//1024//1024}MB 不是预期的32MB")

    if not verify_orig(rom):
        print("[!] 原厂表校验失败! 该ROM可能不是 ARN39P 2.31 原版, 拒绝继续")
        sys.exit(1)
    print("[+] 原厂表校验通过")

    presets = {
        "aggressive": (AGGR_T1, AGGR_T2, AGGR_FAN1_PWM, AGGR_FAN2_PWM, AGGR_FAN1_RPM, AGGR_FAN2_RPM, ORIG_E, ORIG_F),
        "quiet": (QUIET_T1, QUIET_T2, QUIET_FAN1_PWM, QUIET_FAN2_PWM, QUIET_FAN1_RPM, QUIET_FAN2_RPM, ORIG_E, ORIG_F),
        "custom": (CUSTOM_T1, CUSTOM_T2, CUSTOM_FAN1_PWM, CUSTOM_FAN2_PWM, CUSTOM_FAN1_RPM, CUSTOM_FAN2_RPM, CUSTOM_E, CUSTOM_F),
        "restore": (ORIG_T1, ORIG_T2, FAN1_ORIG_PWM, FAN2_ORIG_PWM, FAN1_ORIG_RPM, FAN2_ORIG_RPM, ORIG_E, ORIG_F),
    }
    if mode not in presets:
        print(f"未知模式: {mode} (可选: {'/'.join(presets)})")
        sys.exit(1)

    t1, t2, f1p, f2p, f1r, f2r, e, f = presets[mode]

    write_block(rom, FAN1_BLOCK, t1, t2, f1p, f1r, e, f)
    write_block(rom, FAN2_BLOCK, t1, t2, f2p, f2r, e, f)

    open(dst, 'wb').write(rom)
    print(f"[+] 已生成: {dst} (模式: {mode})")
    print(f"    Fan1 PWM: {f1p[:9]} (最高 {f1p[8]*100//255}%)")
    print(f"    Fan2 PWM: {f2p[:9]} (最高 {f2p[8]*100//255}%)")
    print(f"    升温阈值: {t2[:8]}")
    print(f"    降温阈值: {t1[:9]}")
    print()
    print("[i] 刷写方法:")
    print("    1. 编程器 (T48/TL866等, 闪存芯片 XM25RU256C) —— 最可靠")
    print("    2. EFI: AfuEfix64.efi 新ROM /p /b /n /r /k /l (EC区可能受写保护, 不保证)")
    print("    ⚠️ 刷写有变砖风险! 请确保有编程器救砖手段后再操作!")
    print("    ⚠️ 强烈建议先用方案A(运行时控制)验证效果再考虑刷写!")


if __name__ == "__main__":
    main()
