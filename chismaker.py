#!/usr/bin/env python3
"""
SuperChis 自动量产工具 V2
功能：
1. 多设备管理（自动连接、断开、状态指示）
2. 可配置自动质检（SRAM读写、Flash擦除、PPB解锁、Backup Flash检测等）
3. 选定文件自动量产（直接擦除和写入ROM）
"""

import sys
import os
import time
import struct
import threading
import traceback
import random
from pathlib import Path
from typing import List, Dict, Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QLabel, QFileDialog,
    QProgressBar, QTextEdit, QGroupBox, QCheckBox, QSpinBox, QMessageBox,
    QSplitter
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor

import serial.tools.list_ports
import serial

# 导入设备适配器
from device_adapter import SuperChisDevice


# 质检配置类
class QualityCheckConfig:
    """质检配置"""
    def __init__(self):
        self.enable_sram_test = False          # SRAM读写检测
        self.enable_flash_erase = False        # Flash擦除查空
        self.enable_ppb_unlock = True         # Flash PPB解锁
        self.enable_fast_flash = False        # 快速Flash质检（首尾4M+随机8M）
        self.enable_full_sram = True         # 全量SRAM检测（128KB）
        self.enable_ram_flash = False         # Backup Flash检测


class DeviceInfo:
    """设备信息类"""
    def __init__(self, port_name: str):
        self.port_name = port_name
        self.serial: Optional[serial.Serial] = None
        self.status = "未连接"
        self.rom_id = None
        self.cfi_info = None
        self.last_operation = ""
        self.error_count = 0
    
    def connect(self) -> bool:
        """连接设备"""
        try:
            self.serial = serial.Serial()
            self.serial.port = self.port_name
            self.serial.baudrate = 115200
            self.serial.timeout = 5
            self.serial.open()
            self.serial.dtr = True
            self.serial.dtr = False
            self.status = "已连接"
            return True
        except Exception as e:
            self.status = f"连接失败: {e}"
            return False
    
    def disconnect(self):
        """断开设备"""
        if self.serial and self.serial.is_open:
            self.serial.close()
        self.status = "未连接"
    
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self.serial is not None and self.serial.is_open


class DeviceScanner(QThread):
    """设备扫描线程"""
    devices_found = Signal(list)
    
    def __init__(self):
        super().__init__()
        self.running = True
    
    def run(self):
        """扫描设备"""
        while self.running:
            ports = serial.tools.list_ports.comports()
            device_ports = []
            
            for port in ports:
                if port.vid == 0x0483 and port.pid == 0x0721:
                    device_ports.append(port.device)
            
            self.devices_found.emit(device_ports)
            time.sleep(2)  # 每2秒扫描一次
    
    def stop(self):
        """停止扫描"""
        self.running = False


class QualityCheckWorker(QThread):
    """质检工作线程"""
    log_signal = Signal(str, str)  # (device_name, message)
    progress_signal = Signal(str, int)  # (device_name, progress)
    finished_signal = Signal(str, bool)  # (device_name, success)
    
    def __init__(self, device: DeviceInfo, config: QualityCheckConfig):
        super().__init__()
        self.device = device
        self.config = config
        self.running = True
    
    def stop(self):
        """停止线程"""
        self.running = False
    
    def run(self):
        """执行质检流程"""
        # 创建设备适配器
        device_adapter = SuperChisDevice(self.device.serial)
        
        try:
            self.log_signal.emit(self.device.port_name, "开始质检...")
            
            # 计算启用的测试项数量
            test_count = sum([
                self.config.enable_sram_test,
                self.config.enable_flash_erase or self.config.enable_fast_flash,
                self.config.enable_ppb_unlock,
                self.config.enable_full_sram,
                self.config.enable_ram_flash
            ])
            
            if test_count == 0:
                self.log_signal.emit(self.device.port_name, "未启用任何测试项")
                self.finished_signal.emit(self.device.port_name, False)
                return
            
            current_test = 0
            progress_per_test = 100 / test_count
            
            # 1. SRAM读写检测（基础）
            if self.config.enable_sram_test and self.running:
                current_test += 1
                self.log_signal.emit(self.device.port_name, f"{current_test}/{test_count} SRAM基础读写检测...")
                self.progress_signal.emit(self.device.port_name, int((current_test - 0.5) * progress_per_test))
                
                device_adapter.sram_bank_select(0)
                time.sleep(0.01)
                
                test_data = b'\xAA\x55\x12\x34'
                device_adapter.writeRam(0x0000, test_data)
                time.sleep(0.01)
                read_data = device_adapter.readRam(0x0000, len(test_data))
                
                if read_data != test_data:
                    raise Exception(f"SRAM基础测试失败")
                
                self.log_signal.emit(self.device.port_name, "✓ SRAM基础测试通过")
                self.progress_signal.emit(self.device.port_name, int(current_test * progress_per_test))
            
            # 2. 全量SRAM检测（可选）
            if self.config.enable_full_sram and self.running:
                current_test += 1
                self.log_signal.emit(self.device.port_name, f"{current_test}/{test_count} 全量SRAM检测(128KB)...")
                self.progress_signal.emit(self.device.port_name, int((current_test - 0.5) * progress_per_test))

                # 测试128KB
                device_adapter.sram_bank_select(0)
                time.sleep(0.01)
                
                # 写入测试数据
                chunk_size = 1024
                for addr in range(0, 65536*2, chunk_size):
                    if addr % (65536) == 0:
                        device_adapter.sram_bank_select(addr // 65536)
                        self.log_signal.emit(self.device.port_name, 
                            f"  切换到SRAM Bank {addr // 65536}")
                    # 生成测试数据
                    test_data = bytes([(addr + i) & 0xFF for i in range(chunk_size)])
                    device_adapter.writeRam(addr, test_data)
                    
                    # 立即验证
                    read_data = device_adapter.readRam(addr, chunk_size)
                    if read_data != test_data:
                        raise Exception(f"SRAM全量测试失败于地址 0x{addr:04X}")
                
                self.log_signal.emit(self.device.port_name, "✓ SRAM全量测试通过(128KB)")
                self.progress_signal.emit(self.device.port_name, int(current_test * progress_per_test))
            
            # 3. Flash PPB解锁（在Flash操作之前）
            if self.config.enable_ppb_unlock and self.running:
                current_test += 1
                self.log_signal.emit(self.device.port_name, f"{current_test}/{test_count} Flash PPB解锁...")
                self.progress_signal.emit(self.device.port_name, int((current_test - 0.5) * progress_per_test))
                
                device_adapter.set_sc_mode(sdram=0, sd_enable=0, write_enable=1)
                device_adapter.set_flashmapping([0, 1, 2, 3, 4, 5, 6, 7])
                device_adapter.unlockPPB()
                
                self.log_signal.emit(self.device.port_name, "✓ PPB解锁完成")
                self.progress_signal.emit(self.device.port_name, int(current_test * progress_per_test))
            
            # 4. Flash擦除查空或快速Flash质检
            if (self.config.enable_flash_erase or self.config.enable_fast_flash) and self.running:
                current_test += 1
                
                device_adapter.set_sc_mode(sdram=0, sd_enable=0, write_enable=1)
                device_adapter.set_flashmapping([0, 1, 2, 3, 4, 5, 6, 7])
                
                if self.config.enable_fast_flash:
                    # 快速Flash质检：首尾4M + 随机8M
                    self.log_signal.emit(self.device.port_name, f"{current_test}/{test_count} 快速Flash质检(首尾4M+随机8M)...")
                    self.progress_signal.emit(self.device.port_name, int((current_test - 0.5) * progress_per_test))
                    
                    # 获取CFI信息
                    cfi_info = device_adapter.getRomCFI()
                    sector_size = cfi_info['sector_size']
                    device_size = cfi_info['device_size']
                    
                    # 计算需要擦除的区域
                    test_regions = []
                    
                    # 首4M
                    test_regions.append((0, 4 * 1024 * 1024))
                    
                    # 尾4M
                    end_start = device_size - 4 * 1024 * 1024
                    test_regions.append((end_start, device_size))
                    
                    # 随机8M（分成4个2M区域）
                    random.seed(int(time.time()))
                    for _ in range(4):
                        random_start = random.randint(4 * 1024 * 1024, device_size - 6 * 1024 * 1024)
                        random_start = (random_start // sector_size) * sector_size  # 对齐扇区
                        test_regions.append((random_start, random_start + 2 * 1024 * 1024))
                    
                    # 擦除测试区域
                    total_sectors = 0
                    for start, end in test_regions:
                        sector_count = (end - start) // sector_size
                        total_sectors += sector_count
                    
                    erased_sectors = 0
                    for start, end in test_regions:
                        sector_count = (end - start) // sector_size
                        for i in range(sector_count):
                            addr = start + i * sector_size
                            device_adapter.eraseSector(addr >> 1, sector_size >> 1)
                            erased_sectors += 1
                            
                            if erased_sectors % 10 == 0:
                                self.log_signal.emit(self.device.port_name, 
                                    f"已擦除 {erased_sectors}/{total_sectors} 扇区")
                    
                    # 验证擦除结果
                    for start, end in test_regions:
                        for addr in range(start, end, 4096):
                            data = device_adapter.readRom(addr >> 1, min(4096, end - addr))
                            if not all(b == 0xff for b in data):
                                raise Exception(f"Flash擦除验证失败于地址 0x{addr:08X}")
                    
                    self.log_signal.emit(self.device.port_name, "✓ 快速Flash质检通过(16MB)")
                    
                else:
                    # 全片擦除查空
                    self.log_signal.emit(self.device.port_name, f"{current_test}/{test_count} Flash擦除查空...")
                    self.progress_signal.emit(self.device.port_name, int((current_test - 0.5) * progress_per_test))
                    
                    # 检查第一个扇区是否为空
                    first_sector = device_adapter.readRom(0x000000, 512)
                    is_empty = all(b == 0xff for b in first_sector)
                    
                    if not is_empty:
                        self.log_signal.emit(self.device.port_name, "Flash不为空，执行全片擦除...")
                        device_adapter.eraseChip()
                        self.log_signal.emit(self.device.port_name, "✓ Flash擦除完成")
                    else:
                        self.log_signal.emit(self.device.port_name, "✓ Flash已为空")
                
                self.progress_signal.emit(self.device.port_name, int(current_test * progress_per_test))
            
            # 5. Backup Flash检测（RAM地址映射到Flash）
            if self.config.enable_ram_flash and self.running:
                current_test += 1
                self.log_signal.emit(self.device.port_name, f"{current_test}/{test_count} Backup Flash检测...")
                self.progress_signal.emit(self.device.port_name, int((current_test - 0.5) * progress_per_test))
                
                # 测试RAM地址是否映射到Flash
                # 通常0x0A000000-0x0AFFFFFF应该映射到Flash
                test_patterns = [
                    (0x5555, 0xAA),
                    (0x2AAA, 0x55),
                    (0x0000, 0x90),  # Flash ID命令
                ]
                
                for addr, value in test_patterns:
                    device_adapter.writeRam(addr, value)
                    time.sleep(0.001)
                
                # 读取Flash ID通过RAM接口
                manufacturer = device_adapter.readRam(0x0000, 2)
                device_id = device_adapter.readRam(0x0002, 2)
                
                # 退出ID模式
                device_adapter.writeRam(0x0000, 0xF0)
                
                # 验证读取到有效ID
                if manufacturer != b'\xff\xff' and device_id != b'\xff\xff':
                    self.log_signal.emit(self.device.port_name, 
                        f"✓ Backup Flash检测通过 (Mfr: {manufacturer.hex()}, ID: {device_id.hex()})")
                else:
                    raise Exception("Backup Flash检测失败：无法读取Flash ID")
                
                self.progress_signal.emit(self.device.port_name, int(current_test * progress_per_test))
            
            self.progress_signal.emit(self.device.port_name, 100)
            self.log_signal.emit(self.device.port_name, "质检完成 ✓")
            self.finished_signal.emit(self.device.port_name, True)
            
        except Exception as e:
            self.log_signal.emit(self.device.port_name, f"质检失败: {e}")
            self.finished_signal.emit(self.device.port_name, False)


class ResetNorWorker(QThread):
    """重置NOR游戏工作线程"""
    log_signal = Signal(str, str)  # (device_name, message)
    progress_signal = Signal(str, int)  # (device_name, progress)
    finished_signal = Signal(str, bool)  # (device_name, success)
    
    def __init__(self, device: DeviceInfo):
        super().__init__()
        self.device = device
        self.running = True
    
    def stop(self):
        """停止线程"""
        self.running = False
    
    def run(self):
        """执行重置NOR游戏流程"""
        device_adapter = SuperChisDevice(self.device.serial)
        
        try:
            self.log_signal.emit(self.device.port_name, "开始重置NOR游戏...")
            self.progress_signal.emit(self.device.port_name, 10)
            
            # 设置写使能模式
            device_adapter.set_sc_mode(sdram=0, sd_enable=0, write_enable=1)
            device_adapter.set_flashmapping([0, 1, 2, 3, 4, 5, 6, 7])
            self.log_signal.emit(self.device.port_name, "设置Flash映射完成")
            self.progress_signal.emit(self.device.port_name, 20)
            
            # 解锁PPB保护
            self.log_signal.emit(self.device.port_name, "解锁Flash PPB保护...")
            device_adapter.unlockPPB()
            self.progress_signal.emit(self.device.port_name, 30)
            
            # 擦除Flash元数据区域
            self.log_signal.emit(self.device.port_name, "正在擦除Flash元数据区域(2MB)...")
            device_adapter.eraseFlashMetadata()
            self.progress_signal.emit(self.device.port_name, 80)
            
            # 验证擦除结果
            self.log_signal.emit(self.device.port_name, "验证擦除结果...")
            meta_start_word = 0x00200000 >> 1
            verify_data = device_adapter.readRom(meta_start_word, 512)
            
            if not all(b == 0xff for b in verify_data):
                raise Exception("Flash元数据区域擦除验证失败")
            
            self.log_signal.emit(self.device.port_name, "重置NOR游戏完成 ✓")
            self.progress_signal.emit(self.device.port_name, 100)
            self.finished_signal.emit(self.device.port_name, True)
            
        except Exception as e:
            traceback.print_exc()
            self.log_signal.emit(self.device.port_name, f"重置NOR游戏失败: {e}")
            self.finished_signal.emit(self.device.port_name, False)


class BackupFlashWorker(QThread):
    """备份Flash工作线程"""
    log_signal = Signal(str, str)  # (device_name, message)
    progress_signal = Signal(str, int)  # (device_name, progress)
    finished_signal = Signal(str, bool)  # (device_name, success)
    
    def __init__(self, device: DeviceInfo, backup_file: str, backup_size: int = 128 * 1024 * 1024):
        super().__init__()
        self.device = device
        self.backup_file = backup_file
        self.backup_size = backup_size  # 默认128MB
        self.running = True
    
    def stop(self):
        """停止线程"""
        self.running = False
    
    def run(self):
        """执行备份流程"""
        device_adapter = SuperChisDevice(self.device.serial)
        
        try:
            self.log_signal.emit(self.device.port_name, "开始备份Flash...")
            self.progress_signal.emit(self.device.port_name, 0)
            
            # 设置Flash读取模式
            device_adapter.set_sc_mode(sdram=0, sd_enable=0, write_enable=0)
            device_adapter.set_flashmapping([0, 1, 2, 3, 4, 5, 6, 7])
            time.sleep(0.01)
            
            self.log_signal.emit(self.device.port_name, "设置Flash映射完成")
            
            # 获取CFI信息
            cfi_info = device_adapter.getRomCFI()
            device_size = cfi_info['device_size']
            
            self.log_signal.emit(self.device.port_name, f"Flash大小: {device_size // (1024*1024)}MB")
            
            # 使用指定大小或设备大小中较小的
            total_size = min(self.backup_size, device_size)
            self.log_signal.emit(self.device.port_name, f"备份大小: {total_size // (1024*1024)}MB")
            
            # 打开文件准备写入
            with open(self.backup_file, 'wb') as f:
                read_chunk_size = 4096  # 每次读取4KB
                read_bytes = 0
                
                # 按32MB分段切换映射
                segment_size = 32 * 1024 * 1024
                current_segment = 0
                
                while read_bytes < total_size and self.running:
                    # 检查是否需要切换Flash映射
                    new_segment = read_bytes // segment_size
                    if new_segment != current_segment:
                        current_segment = new_segment
                        mapping_offset = current_segment * 8
                        device_adapter.set_flashmapping([
                            0 + mapping_offset, 1 + mapping_offset, 2 + mapping_offset, 3 + mapping_offset,
                            4 + mapping_offset, 5 + mapping_offset, 6 + mapping_offset, 7 + mapping_offset
                        ])
                        self.log_signal.emit(self.device.port_name, f"切换映射到段 {current_segment}")
                    
                    # 计算当前段内的偏移
                    offset_in_segment = read_bytes % segment_size
                    addr_word = offset_in_segment >> 1
                    
                    # 计算本次读取长度
                    remaining = total_size - read_bytes
                    chunk_size = min(read_chunk_size, remaining)
                    
                    # 读取数据
                    data = device_adapter.readRom(addr_word, chunk_size)
                    f.write(data)
                    
                    read_bytes += chunk_size
                    
                    # 更新进度(每1MB输出一次)
                    if read_bytes % (1024 * 1024) == 0 or read_bytes >= total_size:
                        progress = int(read_bytes / total_size * 100)
                        self.progress_signal.emit(self.device.port_name, progress)
                        self.log_signal.emit(self.device.port_name, 
                            f"已备份 {read_bytes // (1024*1024)}MB / {total_size // (1024*1024)}MB ({progress}%)")
            
            if not self.running:
                self.log_signal.emit(self.device.port_name, "备份已取消")
                self.finished_signal.emit(self.device.port_name, False)
                return
            
            self.log_signal.emit(self.device.port_name, "备份完成 ✓")
            self.progress_signal.emit(self.device.port_name, 100)
            self.finished_signal.emit(self.device.port_name, True)
            
        except Exception as e:
            traceback.print_exc()
            self.log_signal.emit(self.device.port_name, f"备份失败: {e}")
            self.finished_signal.emit(self.device.port_name, False)


class ProductionWorker(QThread):
    """量产工作线程"""
    log_signal = Signal(str, str)  # (device_name, message)
    progress_signal = Signal(str, int)  # (device_name, progress)
    finished_signal = Signal(str, bool)  # (device_name, success)
    
    def __init__(self, device: DeviceInfo, rom_file: str):
        super().__init__()
        self.device = device
        self.rom_file = rom_file
        self.running = True
    
    def stop(self):
        """停止线程"""
        self.running = False
    
    def run(self):
        """执行量产流程"""
        # 创建设备适配器
        device_adapter = SuperChisDevice(self.device.serial)
        
        try:
            self.log_signal.emit(self.device.port_name, "开始量产...")
            
            # 读取ROM文件
            with open(self.rom_file, 'rb') as f:
                rom_data = f.read()
            
            file_size = len(rom_data)
            self.log_signal.emit(self.device.port_name, f"ROM文件大小: {file_size} 字节")
            
            # 对齐到偶数
            if file_size % 2 != 0:
                rom_data += b'\x00'
                file_size = len(rom_data)
            
            # 切换到Flash模式
            device_adapter.set_sc_mode(sdram=0, sd_enable=0, write_enable=1)
            device_adapter.set_flashmapping([0, 1, 2, 3, 4, 5, 6, 7])
            time.sleep(0.01)
            
            self.log_signal.emit(self.device.port_name, "开始量产写入...")
            
            # 获取CFI信息
            cfi_info = device_adapter.getRomCFI()
            sector_size = cfi_info['sector_size']
            buffer_size = cfi_info['buffer_write_bytes']
            
            self.log_signal.emit(self.device.port_name, f"扇区大小: {sector_size} 字节, Buffer大小: {buffer_size} 字节")
            
            # 擦除需要的扇区 - 需要处理32MB分段
            addr_end = file_size - 1
            sector_count = (addr_end // sector_size) + 1
            segment_size = 32 * 1024 * 1024
            
            self.log_signal.emit(self.device.port_name, f"擦除 {sector_count} 个扇区...")
            
            current_segment = 0
            for i in range(sector_count):
                if not self.running:
                    self.log_signal.emit(self.device.port_name, "擦除已取消")
                    self.finished_signal.emit(self.device.port_name, False)
                    return
                
                addr = i * sector_size
                
                # 检查是否需要切换Flash映射
                new_segment = addr // segment_size
                if new_segment != current_segment:
                    current_segment = new_segment
                    mapping_offset = current_segment * 8
                    device_adapter.set_flashmapping([
                        0 + mapping_offset, 1 + mapping_offset, 2 + mapping_offset, 3 + mapping_offset,
                        4 + mapping_offset, 5 + mapping_offset, 6 + mapping_offset, 7 + mapping_offset
                    ])
                    self.log_signal.emit(self.device.port_name, f"切换映射到段 {current_segment} (映射偏移: {mapping_offset})")
                
                # 计算当前段内的偏移
                offset_in_segment = addr % segment_size
                device_adapter.eraseSector(offset_in_segment >> 1, sector_size >> 1)
                
                progress = int((i + 1) / sector_count * 50)
                self.progress_signal.emit(self.device.port_name, progress)
                
                if (i + 1) % 10 == 0 or (i + 1) == sector_count:
                    self.log_signal.emit(self.device.port_name, 
                        f"已擦除 {i + 1}/{sector_count} 扇区")
            
            self.log_signal.emit(self.device.port_name, "擦除完成，开始写入...")
            
            # 重新设置写使能模式和映射（擦除后可能需要重新设置）
            device_adapter.set_sc_mode(sdram=0, sd_enable=0, write_enable=1)
            device_adapter.set_flashmapping([0, 1, 2, 3, 4, 5, 6, 7])
            time.sleep(0.01)
            
            # 写入数据 - 按32MB分段切换映射
            write_chunk_size = 2048
            written = 0
            segment_size = 32 * 1024 * 1024
            current_segment = 0
            
            while written < file_size and self.running:
                # 检查是否需要切换Flash映射
                new_segment = written // segment_size
                if new_segment != current_segment:
                    current_segment = new_segment
                    mapping_offset = current_segment * 8
                    device_adapter.set_flashmapping([
                        0 + mapping_offset, 1 + mapping_offset, 2 + mapping_offset, 3 + mapping_offset,
                        4 + mapping_offset, 5 + mapping_offset, 6 + mapping_offset, 7 + mapping_offset
                    ])
                    self.log_signal.emit(self.device.port_name, f"切换映射到段 {current_segment}")
                
                # 计算当前段内的偏移地址
                offset_in_segment = written % segment_size
                
                # 准备写入数据
                chunk = rom_data[written:written + write_chunk_size]
                if len(chunk) < write_chunk_size:
                    chunk += b'\xff' * (write_chunk_size - len(chunk))
                
                # 使用字节地址调用programRom（0xf4命令使用字节地址）
                result = device_adapter.programRom(offset_in_segment, chunk, buffer_size)
                if not result:
                    raise Exception(f"写入失败于地址 0x{written:08X} (段{current_segment}, 偏移0x{offset_in_segment:08X})")
                
                written += write_chunk_size
                
                # 每写入64KB输出一次日志
                if written % (64 * 1024) == 0 or written >= file_size:
                    self.log_signal.emit(self.device.port_name, 
                        f"已写入 {written}/{file_size} 字节 ({written*100//file_size}%)")
                
                progress = 50 + int(written / file_size * 50)
                self.progress_signal.emit(self.device.port_name, progress)
            
            if not self.running:
                self.log_signal.emit(self.device.port_name, "量产已取消")
                self.finished_signal.emit(self.device.port_name, False)
                return
            
            self.log_signal.emit(self.device.port_name, "写入完成，开始校验...")
            
            # 校验 - 同样需要处理32MB分段
            device_adapter.set_flashmapping([0, 1, 2, 3, 4, 5, 6, 7])  # 重置映射到开始
            verify_chunk_size = 4096
            verified = 0
            current_segment = 0
            
            while verified < file_size and self.running:
                # 检查是否需要切换Flash映射
                new_segment = verified // segment_size
                if new_segment != current_segment:
                    current_segment = new_segment
                    mapping_offset = current_segment * 8
                    device_adapter.set_flashmapping([
                        0 + mapping_offset, 1 + mapping_offset, 2 + mapping_offset, 3 + mapping_offset,
                        4 + mapping_offset, 5 + mapping_offset, 6 + mapping_offset, 7 + mapping_offset
                    ])
                
                # 计算当前段内的偏移
                offset_in_segment = verified % segment_size
                addr_word = offset_in_segment >> 1
                
                # 计算本次校验长度
                length = min(verify_chunk_size, file_size - verified)
                expected = rom_data[verified:verified + length]
                
                # 读取并校验
                actual = device_adapter.readRom(addr_word, length)
                if actual != expected:
                    raise Exception(f"校验失败于地址 0x{verified:08X}")
                
                verified += length
                
                # 每校验1MB输出一次日志
                if verified % (1024 * 1024) == 0 or verified >= file_size:
                    self.log_signal.emit(self.device.port_name, 
                        f"已校验 {verified // (1024*1024)}MB / {file_size // (1024*1024)}MB")
            
            self.log_signal.emit(self.device.port_name, "量产完成 ✓")
            self.progress_signal.emit(self.device.port_name, 100)
            self.finished_signal.emit(self.device.port_name, True)
            
        except Exception as e:
            traceback.print_exc()
            self.log_signal.emit(self.device.port_name, f"量产失败: {e}")
            self.finished_signal.emit(self.device.port_name, False)


class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        self.devices: Dict[str, DeviceInfo] = {}
        self.workers: Dict[str, QThread] = {}
        self.rom_file = ""
        self.quality_config = QualityCheckConfig()
        self.scanner = None
        
        self.init_ui()
        
        # 启动设备扫描
        self.scanner = DeviceScanner()
        self.scanner.devices_found.connect(self.on_devices_found)
        self.scanner.start()
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("SuperChis 自动量产工具 V2")
        self.setGeometry(100, 100, 1400, 800)
        
        # 主布局 - 使用分隔器
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # 创建分隔器
        splitter = QSplitter(Qt.Horizontal)
        
        # 左侧边栏 - 质检配置
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        # 质检配置组
        config_group = QGroupBox("质检配置")
        config_layout = QVBoxLayout()
        
        self.check_sram_basic = QCheckBox("SRAM基础读写检测")
        self.check_sram_basic.setChecked(self.quality_config.enable_sram_test)
        self.check_sram_basic.stateChanged.connect(self.on_sram_basic_changed)
        
        self.check_sram_full = QCheckBox("SRAM全量检测(128KB)")
        self.check_sram_full.setChecked(self.quality_config.enable_full_sram)
        self.check_sram_full.stateChanged.connect(self.on_sram_full_changed)
        
        self.check_flash_erase = QCheckBox("Flash全片擦除查空")
        self.check_flash_erase.setChecked(self.quality_config.enable_flash_erase)
        self.check_flash_erase.stateChanged.connect(self.on_flash_erase_changed)
        
        self.check_flash_fast = QCheckBox("Flash快速质检(16MB)")
        self.check_flash_fast.setChecked(self.quality_config.enable_fast_flash)
        self.check_flash_fast.stateChanged.connect(self.on_flash_fast_changed)
        
        self.check_ppb = QCheckBox("Flash PPB解锁")
        self.check_ppb.setChecked(self.quality_config.enable_ppb_unlock)
        self.check_ppb.stateChanged.connect(self.on_ppb_changed)
        
        self.check_ram_flash = QCheckBox("Backup Flash检测")
        self.check_ram_flash.setChecked(self.quality_config.enable_ram_flash)
        self.check_ram_flash.stateChanged.connect(self.on_ram_flash_changed)
        
        config_layout.addWidget(self.check_sram_basic)
        config_layout.addWidget(self.check_sram_full)
        config_layout.addWidget(self.check_flash_erase)
        config_layout.addWidget(self.check_flash_fast)
        config_layout.addWidget(self.check_ppb)
        config_layout.addWidget(self.check_ram_flash)
        config_layout.addStretch()
        
        config_group.setLayout(config_layout)
        left_layout.addWidget(config_group)
        
        # 质检说明
        desc_group = QGroupBox("功能说明")
        desc_layout = QVBoxLayout()
        desc_text = QTextEdit()
        desc_text.setReadOnly(True)
        desc_text.setMaximumHeight(200)
        desc_text.setHtml("""
        <b>SRAM基础：</b>快速测试SRAM Bank0前4字节<br>
        <b>SRAM全量：</b>完整测试128KB SRAM<br>
        <b>Flash全片擦除：</b>检查并擦除整个Flash<br>
        <b>Flash快速质检：</b>仅擦除首尾4M+随机8M(共16MB)<br>
        <b>PPB解锁：</b>解除Flash写保护<br>
        <b>RAM Flash：</b>测试RAM接口访问Flash
        """)
        desc_layout.addWidget(desc_text)
        desc_group.setLayout(desc_layout)
        left_layout.addWidget(desc_group)
        
        left_panel.setMaximumWidth(300)
        
        # 右侧主面板
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # 顶部控制区
        control_group = QGroupBox("控制面板")
        control_layout = QHBoxLayout()
        
        # ROM文件选择
        self.file_label = QLabel("未选择ROM文件")
        self.select_file_btn = QPushButton("选择ROM文件")
        self.select_file_btn.clicked.connect(self.select_rom_file)
        
        # 操作按钮
        self.connect_all_btn = QPushButton("连接所有设备")
        self.connect_all_btn.clicked.connect(self.connect_all_devices)
        
        self.disconnect_all_btn = QPushButton("断开所有设备")
        self.disconnect_all_btn.clicked.connect(self.disconnect_all_devices)
        
        self.quality_check_btn = QPushButton("批量质检")
        self.quality_check_btn.clicked.connect(self.start_quality_check_all)
        
        self.production_btn = QPushButton("批量量产")
        self.production_btn.clicked.connect(self.start_production_all)
        self.production_btn.setEnabled(False)
        
        self.reset_nor_btn = QPushButton("重置NOR游戏")
        self.reset_nor_btn.clicked.connect(self.start_reset_nor_all)
        self.reset_nor_btn.setStyleSheet("QPushButton { background-color: #FF6B6B; color: white; font-weight: bold; }")
        
        self.backup_flash_btn = QPushButton("备份Flash")
        self.backup_flash_btn.clicked.connect(self.start_backup_flash_all)
        self.backup_flash_btn.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; }")
        
        control_layout.addWidget(self.select_file_btn)
        control_layout.addWidget(self.file_label)
        control_layout.addStretch()
        control_layout.addWidget(self.connect_all_btn)
        control_layout.addWidget(self.disconnect_all_btn)
        control_layout.addWidget(self.quality_check_btn)
        control_layout.addWidget(self.production_btn)
        control_layout.addWidget(self.reset_nor_btn)
        control_layout.addWidget(self.backup_flash_btn)
        
        control_group.setLayout(control_layout)
        right_layout.addWidget(control_group)
        
        # 设备列表
        device_group = QGroupBox("设备列表")
        device_layout = QVBoxLayout()
        
        self.device_table = QTableWidget()
        self.device_table.setColumnCount(5)
        self.device_table.setHorizontalHeaderLabels(["端口", "状态", "进度", "操作", "日志"])
        self.device_table.setColumnWidth(0, 120)
        self.device_table.setColumnWidth(1, 80)
        self.device_table.setColumnWidth(2, 160)
        self.device_table.setColumnWidth(3, 350)
        self.device_table.setColumnWidth(4, 450)
        
        device_layout.addWidget(self.device_table)
        device_group.setLayout(device_layout)
        right_layout.addWidget(device_group)
        
        # 全局日志
        log_group = QGroupBox("全局日志")
        log_layout = QVBoxLayout()
        
        self.global_log = QTextEdit()
        self.global_log.setReadOnly(True)
        self.global_log.setMaximumHeight(150)
        
        log_layout.addWidget(self.global_log)
        log_group.setLayout(log_layout)
        right_layout.addWidget(log_group)
        
        # 添加到分隔器
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        
        main_layout.addWidget(splitter)
    
    def on_sram_basic_changed(self, state):
        """SRAM基础测试选项变更"""
        self.quality_config.enable_sram_test = (state == Qt.CheckState.Checked.value)
        self.log_global(f"SRAM基础测试: {'启用' if self.quality_config.enable_sram_test else '禁用'}")
    
    def on_sram_full_changed(self, state):
        """SRAM全量测试选项变更"""
        self.quality_config.enable_full_sram = (state == Qt.CheckState.Checked.value)
        self.log_global(f"SRAM全量测试: {'启用' if self.quality_config.enable_full_sram else '禁用'}")
    
    def on_flash_erase_changed(self, state):
        """Flash擦除选项变更"""
        self.quality_config.enable_flash_erase = (state == Qt.CheckState.Checked.value)
        if self.quality_config.enable_flash_erase:
            self.quality_config.enable_fast_flash = False
            self.check_flash_fast.setChecked(False)
        self.log_global(f"Flash全片擦除: {'启用' if self.quality_config.enable_flash_erase else '禁用'}")
    
    def on_flash_fast_changed(self, state):
        """Flash快速质检选项变更"""
        self.quality_config.enable_fast_flash = (state == Qt.CheckState.Checked.value)
        if self.quality_config.enable_fast_flash:
            self.quality_config.enable_flash_erase = False
            self.check_flash_erase.setChecked(False)
        self.log_global(f"Flash快速质检: {'启用' if self.quality_config.enable_fast_flash else '禁用'}")
    
    def on_ppb_changed(self, state):
        """PPB解锁选项变更"""
        self.quality_config.enable_ppb_unlock = (state == Qt.CheckState.Checked.value)
        self.log_global(f"PPB解锁: {'启用' if self.quality_config.enable_ppb_unlock else '禁用'}")
    
    def on_ram_flash_changed(self, state):
        """Backup Flash检测选项变更"""
        self.quality_config.enable_ram_flash = (state == Qt.CheckState.Checked.value)
        self.log_global(f"Backup Flash检测: {'启用' if self.quality_config.enable_ram_flash else '禁用'}")
    
    def select_rom_file(self):
        """选择ROM文件"""
        file_name, _ = QFileDialog.getOpenFileName( #gba或者bin格式
            self, "选择ROM文件", "", "ROM Files (*.gba *.bin);;All Files (*)"
        )
        
        if file_name:
            self.rom_file = file_name
            self.file_label.setText(f"已选择: {Path(file_name).name}")
            self.production_btn.setEnabled(True)
            self.log_global(f"已选择ROM文件: {file_name}")
    
    def on_devices_found(self, ports: List[str]):
        """设备扫描回调"""
        # 移除已断开的设备
        removed_ports = [p for p in self.devices.keys() if p not in ports]
        for port in removed_ports:
            self.devices[port].disconnect()
            del self.devices[port]
        
        # 添加新设备
        new_device_added = False
        for port in ports:
            if port not in self.devices:
                self.devices[port] = DeviceInfo(port)
                self.log_global(f"发现新设备: {port}")
                new_device_added = True
        
        # 当设备列表变化时，需要完全重建表格
        self.update_device_table(full_rebuild=(len(removed_ports) > 0 or new_device_added))
    
    def update_device_table(self, full_rebuild=False):
        """更新设备表格
        Args:
            full_rebuild: 是否完全重建表格（包括widget）
        """
        # 记录当前进度条的值
        progress_values = {}
        if not full_rebuild:
            for row in range(self.device_table.rowCount()):
                port_item = self.device_table.item(row, 0)
                if port_item:
                    port = port_item.text()
                    progress_bar = self.device_table.cellWidget(row, 2)
                    if progress_bar:
                        progress_values[port] = progress_bar.value()
        
        self.device_table.setRowCount(len(self.devices))
        
        for row, (port, device) in enumerate(self.devices.items()):
            # 端口
            self.device_table.setItem(row, 0, QTableWidgetItem(port))
            
            # 状态
            status_item = QTableWidgetItem(device.status)
            if "已连接" in device.status:
                status_item.setBackground(QColor(144, 238, 144))
            elif "失败" in device.status:
                status_item.setBackground(QColor(255, 182, 193))
            self.device_table.setItem(row, 1, status_item)
            
            # 进度条 - 只在必要时重新创建
            if full_rebuild or self.device_table.cellWidget(row, 2) is None:
                progress_bar = QProgressBar()
                progress_bar.setMinimum(0)
                progress_bar.setMaximum(100)
                # 恢复之前的进度值
                progress_bar.setValue(progress_values.get(port, 0))
                self.device_table.setCellWidget(row, 2, progress_bar)
            
            # 操作按钮 - 只在必要时重新创建（每个按钮都绑定到具体的port）
            if full_rebuild or self.device_table.cellWidget(row, 3) is None:
                button_widget = QWidget()
                button_layout = QHBoxLayout(button_widget)
                button_layout.setContentsMargins(2, 2, 2, 2)
                
                connect_btn = QPushButton("连接")
                connect_btn.clicked.connect(lambda checked, p=port: self.connect_device(p))
                
                disconnect_btn = QPushButton("断开")
                disconnect_btn.clicked.connect(lambda checked, p=port: self.disconnect_device(p))
                
                quality_btn = QPushButton("质检")
                quality_btn.clicked.connect(lambda checked, p=port: self.start_quality_check(p))
                
                production_btn = QPushButton("量产")
                production_btn.clicked.connect(lambda checked, p=port: self.start_production(p))
                
                reset_nor_btn = QPushButton("重置NOR")
                reset_nor_btn.clicked.connect(lambda checked, p=port: self.start_reset_nor(p))
                reset_nor_btn.setStyleSheet("background-color: #FF6B6B; color: white;")
                
                backup_btn = QPushButton("备份")
                backup_btn.clicked.connect(lambda checked, p=port: self.start_backup_flash(p))
                backup_btn.setStyleSheet("background-color: #4CAF50; color: white;")
                
                button_layout.addWidget(connect_btn)
                button_layout.addWidget(disconnect_btn)
                button_layout.addWidget(quality_btn)
                button_layout.addWidget(production_btn)
                button_layout.addWidget(reset_nor_btn)
                button_layout.addWidget(backup_btn)
                
                self.device_table.setCellWidget(row, 3, button_widget)
            
            # 日志（使用最后操作）
            self.device_table.setItem(row, 4, QTableWidgetItem(device.last_operation))
    
    def connect_device(self, port: str):
        """连接单个设备"""
        device = self.devices.get(port)
        if device:
            if device.connect():
                self.log_global(f"{port} 连接成功")
            else:
                self.log_global(f"{port} 连接失败")
            self.update_device_table()
    
    def disconnect_device(self, port: str):
        """断开单个设备"""
        device = self.devices.get(port)
        if device:
            device.disconnect()
            self.log_global(f"{port} 已断开")
            self.update_device_table()
    
    def connect_all_devices(self):
        """连接所有设备"""
        for port, device in self.devices.items():
            if not device.is_connected():
                self.connect_device(port)
    
    def disconnect_all_devices(self):
        """断开所有设备"""
        for port in list(self.devices.keys()):
            self.disconnect_device(port)
    
    def start_quality_check(self, port: str):
        """开始单个设备质检"""
        device = self.devices.get(port)
        if not device or not device.is_connected():
            QMessageBox.warning(self, "警告", f"{port} 未连接")
            return
        
        # 如果已有工作线程在运行,先停止它
        if port in self.workers and self.workers[port].isRunning():
            self.workers[port].stop()
            self.workers[port].wait(1000)
        
        worker = QualityCheckWorker(device, self.quality_config)
        worker.log_signal.connect(self.on_worker_log)
        worker.progress_signal.connect(self.on_worker_progress)
        worker.finished_signal.connect(self.on_worker_finished)
        # 确保线程完成时会触发清理
        worker.finished.connect(lambda: self.cleanup_worker(port))
        
        self.workers[port] = worker
        worker.start()
    
    def start_quality_check_all(self):
        """批量质检"""
        delay = 0
        for port, device in self.devices.items():
            if device.is_connected():
                # 使用QTimer添加随机延迟，避免USB供电不足
                QTimer.singleShot(int(delay * 1000), lambda p=port: self.start_quality_check(p))
                delay += random.uniform(0.5, 1.0)  # 每个设备间隔0.5-1秒
    
    def start_production(self, port: str):
        """开始单个设备量产"""
        if not self.rom_file:
            QMessageBox.warning(self, "警告", "请先选择ROM文件")
            return
        
        device = self.devices.get(port)
        if not device or not device.is_connected():
            QMessageBox.warning(self, "警告", f"{port} 未连接")
            return
        
        # 如果已有工作线程在运行,先停止它
        if port in self.workers and self.workers[port].isRunning():
            self.workers[port].stop()
            self.workers[port].wait(1000)
        
        worker = ProductionWorker(device, self.rom_file)
        worker.log_signal.connect(self.on_worker_log)
        worker.progress_signal.connect(self.on_worker_progress)
        worker.finished_signal.connect(self.on_worker_finished)
        # 确保线程完成时会触发清理
        worker.finished.connect(lambda: self.cleanup_worker(port))
        
        self.workers[port] = worker
        worker.start()
    
    def start_production_all(self):
        """批量量产"""
        if not self.rom_file:
            QMessageBox.warning(self, "警告", "请先选择ROM文件")
            return
        
        delay = 0
        for port, device in self.devices.items():
            if device.is_connected():
                # 使用QTimer添加随机延迟，避免USB供电不足
                QTimer.singleShot(int(delay * 1000), lambda p=port: self.start_production(p))
                delay += random.uniform(0.5, 1.0)  # 每个设备间隔0.5-1秒
    
    def start_reset_nor(self, port: str):
        """开始单个设备重置NOR游戏"""
        device = self.devices.get(port)
        if not device or not device.is_connected():
            QMessageBox.warning(self, "警告", f"{port} 未连接")
            return
        
        # 确认操作
        reply = QMessageBox.question(
            self, "确认操作",
            f"确定要重置 {port} 的NOR游戏吗?\n\n这将擦除Flash元数据区域(2MB),操作不可逆!",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 如果已有工作线程在运行,先停止它
        if port in self.workers and self.workers[port].isRunning():
            self.workers[port].stop()
            self.workers[port].wait(1000)
        
        worker = ResetNorWorker(device)
        worker.log_signal.connect(self.on_worker_log)
        worker.progress_signal.connect(self.on_worker_progress)
        worker.finished_signal.connect(self.on_worker_finished)
        worker.finished.connect(lambda: self.cleanup_worker(port))
        
        self.workers[port] = worker
        worker.start()
    
    def start_reset_nor_all(self):
        """批量重置NOR游戏"""
        # 确认操作
        reply = QMessageBox.question(
            self, "确认操作",
            f"确定要对所有已连接设备重置NOR游戏吗?\n\n这将擦除所有设备的Flash元数据区域(2MB),操作不可逆!",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        for port, device in self.devices.items():
            if device.is_connected():
                # 跳过确认对话框,直接执行
                if port in self.workers and self.workers[port].isRunning():
                    self.workers[port].stop()
                    self.workers[port].wait(1000)
                
                worker = ResetNorWorker(device)
                worker.log_signal.connect(self.on_worker_log)
                worker.progress_signal.connect(self.on_worker_progress)
                worker.finished_signal.connect(self.on_worker_finished)
                worker.finished.connect(lambda p=port: self.cleanup_worker(p))
                
                self.workers[port] = worker
                worker.start()
    
    def start_backup_flash(self, port: str):
        """开始单个设备备份Flash"""
        device = self.devices.get(port)
        if not device or not device.is_connected():
            QMessageBox.warning(self, "警告", f"{port} 未连接")
            return
        
        # 选择保存文件
        default_name = f"flash_backup_{port.replace('/', '_')}_{time.strftime('%Y%m%d_%H%M%S')}.bin"
        backup_file, _ = QFileDialog.getSaveFileName(
            self, "保存Flash备份", default_name, "Binary Files (*.bin);;All Files (*)"
        )
        
        if not backup_file:
            return
        
        # 如果已有工作线程在运行,先停止它
        if port in self.workers and self.workers[port].isRunning():
            self.workers[port].stop()
            self.workers[port].wait(1000)
        
        worker = BackupFlashWorker(device, backup_file)
        worker.log_signal.connect(self.on_worker_log)
        worker.progress_signal.connect(self.on_worker_progress)
        worker.finished_signal.connect(self.on_worker_finished)
        worker.finished.connect(lambda: self.cleanup_worker(port))
        
        self.workers[port] = worker
        worker.start()
    
    def start_backup_flash_all(self):
        """批量备份Flash"""
        # 选择保存目录
        backup_dir = QFileDialog.getExistingDirectory(
            self, "选择备份保存目录", "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        
        if not backup_dir:
            return
        
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        
        for port, device in self.devices.items():
            if device.is_connected():
                # 为每个设备生成备份文件名
                safe_port = port.replace('/', '_').replace('\\', '_')
                backup_file = os.path.join(backup_dir, f"flash_backup_{safe_port}_{timestamp}.bin")
                
                # 如果已有工作线程在运行,先停止它
                if port in self.workers and self.workers[port].isRunning():
                    self.workers[port].stop()
                    self.workers[port].wait(1000)
                
                worker = BackupFlashWorker(device, backup_file)
                worker.log_signal.connect(self.on_worker_log)
                worker.progress_signal.connect(self.on_worker_progress)
                worker.finished_signal.connect(self.on_worker_finished)
                worker.finished.connect(lambda p=port: self.cleanup_worker(p))
                
                self.workers[port] = worker
                worker.start()
                
                self.log_global(f"开始备份 {port} 到 {backup_file}")
    
    def on_worker_log(self, port: str, message: str):
        """工作线程日志回调"""
        device = self.devices.get(port)
        if device:
            device.last_operation = message
            # 仅更新文本内容，不重建widget
            for row in range(self.device_table.rowCount()):
                if self.device_table.item(row, 0).text() == port:
                    self.device_table.setItem(row, 4, QTableWidgetItem(message))
                    break
        
        self.log_global(f"[{port}] {message}")
    
    def on_worker_progress(self, port: str, progress: int):
        """工作线程进度回调"""
        for row in range(self.device_table.rowCount()):
            if self.device_table.item(row, 0).text() == port:
                progress_bar = self.device_table.cellWidget(row, 2)
                if progress_bar:
                    progress_bar.setValue(progress)
                break
    
    def on_worker_finished(self, port: str, success: bool):
        """工作线程完成回调"""
        device = self.devices.get(port)
        if device:
            if success:
                device.status = "操作成功"
            else:
                device.status = "操作失败"
                device.error_count += 1
            self.update_device_table()
    
    def cleanup_worker(self, port: str):
        """清理完成的工作线程"""
        if port in self.workers:
            worker = self.workers[port]
            # 确保线程已经完成
            if worker.isRunning():
                worker.wait(2000)  # 等待最多2秒
            # 断开所有信号连接
            try:
                worker.log_signal.disconnect()
                worker.progress_signal.disconnect()
                worker.finished_signal.disconnect()
                worker.finished.disconnect()
            except:
                pass
            # 移除引用,让Python GC可以安全地回收
            del self.workers[port]
    
    def log_global(self, message: str):
        """全局日志"""
        timestamp = time.strftime("%H:%M:%S")
        self.global_log.append(f"[{timestamp}] {message}")
    
    def closeEvent(self, event):
        """关闭窗口"""
        self.log_global("正在关闭程序...")
        
        # 停止扫描线程
        if hasattr(self, 'scanner') and self.scanner.isRunning():
            self.scanner.stop()
            self.scanner.wait(5000)  # 等待最多5秒
            if self.scanner.isRunning():
                self.scanner.terminate()
                self.scanner.wait()
        
        # 停止所有工作线程
        for port, worker in list(self.workers.items()):
            if worker.isRunning():
                worker.stop()
                worker.wait(5000)  # 等待最多5秒
                if worker.isRunning():
                    worker.terminate()
                    worker.wait()
        
        # 清空工作线程字典
        self.workers.clear()
        
        # 断开所有设备
        for device in self.devices.values():
            if device.is_connected():
                device.disconnect()
        
        self.log_global("程序已关闭")
        event.accept()


def main():
    """主函数"""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
