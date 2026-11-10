#!/bin/bash
# EC寄存器"位存活"解剖实验 — 判断哪些位主机可写
P="sudo python3 $PWD/ec_power_fn.py"
echo "=== 实验1: LIDF(0x451)位存活测试 — 写0xFF观察哪些位能活过EC刷新 ==="
sudo python3 ec_power_fn.py i2ec wr 0x0451 0xFF
for i in 1 2 3 4 5; do
  sleep 1
  printf "  %d秒后: " $i
  sudo python3 ec_power_fn.py i2ec rd 0x0451
done
echo
echo "=== 实验2: 7天计时器(0x5C2-0x5C5)写入测试 — 写满0xFF看是否被复位 ==="
for addr in 0x05c2 0x05c3 0x05c4 0x05c5; do
  sudo python3 ec_power_fn.py i2ec wr $addr 0xFF
done
sleep 3
echo "  3秒后回读:"
for addr in 0x05c2 0x05c3 0x05c4 0x05c5; do
  printf "    %s = " $addr
  sudo python3 ec_power_fn.py i2ec rd $addr
done
echo
echo "=== 实验3: 电池满计数器(0x60C)当前值 ==="
printf "  0x060C = "
sudo python3 ec_power_fn.py i2ec rd 0x060C
