# SuperChis 自动量产工具

## 功能特性

### 1. 多设备管理
- ✅ 自动扫描并识别SuperChis烧卡器设备
- ✅ 支持同时连接多个设备
- ✅ 实时状态指示（已连接/未连接/操作中）
- ✅ 批量连接/断开设备
- ✅ 单独控制每个设备

### 2. 自动质检
完整的质检流程包括：
- ✅ **SDRAM读写检测** - 验证SDRAM功能正常
- ✅ **SRAM读写检测** - 验证SRAM Bank切换和读写
- ✅ **Flash擦除查空** - 检查Flash是否为空，不为空则自动擦除
- ✅ **Flash PPB解锁** - 检查并解锁PPB保护位

### 3. 自动量产
智能量产流程：
- ✅ 选择ROM文件（.gba格式）
- ✅ 自动比对Flash头部4K数据
- ✅ 仅在数据不一致时执行写入（提高效率）
- ✅ 自动擦除扇区
- ✅ Buffer Write加速写入
- ✅ 自动校验写入数据
- ✅ 实时进度显示

### 4. 用户界面
- ✅ 清晰的设备列表视图
- ✅ 每个设备独立的进度条
- ✅ 实时日志输出
- ✅ 批量操作支持
- ✅ 操作结果可视化

## 安装依赖

```bash
pip install -r requirements.txt
```

## 使用方法

### 启动程序

```bash
python chismaker.py
```

### 操作步骤

1. **连接设备**
   - 程序会自动扫描USB端口并识别SuperChis设备
   - 点击"连接所有设备"或单独连接每个设备

2. **质检（可选但推荐）**
   - 点击"批量质检"或单独质检某个设备
   - 等待质检完成，检查是否所有项目都通过

3. **量产**
   - 点击"选择ROM文件"选择要烧录的.gba文件
   - 点击"批量量产"开始所有设备的量产
   - 或单独点击某个设备的"量产"按钮

4. **监控进度**
   - 每个设备都有独立的进度条
   - 日志区域显示详细操作信息
   - 状态栏显示当前操作状态

## 技术细节

### cart_adapter.py 增强功能

新增函数：
- `readRomID()` - 读取Flash芯片ID
- `getRomCFI()` - 获取CFI信息（容量、扇区大小等）
- `getRomEraseTime()` - 获取擦除超时时间
- `eraseChip()` - 整片擦除
- `eraseSector()` - 扇区擦除
- `programRom()` - ROM编程（支持Buffer Write）
- `unlockPPB()` - PPB保护位解锁
- `checkPPBLocked()` - 检查PPB锁定状态
- `verifyRom()` - ROM数据校验

### SuperChis解锁机制

使用魔术地址解锁：
- 魔术地址：`0x01FFFFFE` (GBA地址空间)
- 魔术值：`0xA55A`
- Flash映射值：`0xA558`

配置位：
- Bit 0: SDRAM/Flash切换
- Bit 1: SD卡使能
- Bit 2: 写使能
- Bit 3: SRAM Bank选择

### 多线程架构

- `DeviceScanner` - 后台扫描设备线程
- `QualityCheckWorker` - 质检工作线程
- `ProductionWorker` - 量产工作线程

每个设备的操作在独立线程中执行，互不干扰。

## 性能优化

1. **智能写入**
   - 比对头部4K，仅在不一致时写入
   - 跳过已正确写入的卡带

2. **Buffer Write**
   - 自动使用Flash的Buffer Write功能
   - 提高写入速度

3. **并行处理**
   - 多个设备同时操作
   - 充分利用多核CPU

## 故障排除

### 设备未识别
- 检查USB连接
- 确认设备VID:PID为 0x0483:0x0721
- 尝试重新插拔设备

### 质检失败
- SDRAM失败：检查硬件连接
- SRAM失败：检查SRAM芯片
- Flash擦除失败：尝试手动解锁PPB
- PPB解锁失败：检查Flash芯片型号

### 量产失败
- 确保已通过质检
- 检查ROM文件完整性
- 查看详细日志定位问题

## 支持的Flash芯片

基于CFI标准，支持：
- S29GL256 (Spansion)
- JS28F256 (Intel)
- S29GL01 (Spansion)
- S70GL02 (Spansion)
- MX29GL256 (Macronix)
- M29W256G (Micron)
- 其他兼容CFI的Flash芯片

## 开发计划

- [ ] 支持GBC卡带
- [ ] 存档备份/恢复功能
- [ ] 卡带信息自动识别
- [ ] 量产统计报表
- [ ] 配置文件保存/加载

## 许可证

MIT License

## 致谢

基于ChisFlashBurner项目的协议实现。
