"""
SuperChis设备适配器 - 线程安全版本
每个设备实例独立管理serial连接
"""

import struct
import serial
import time
import serial.tools.list_ports


# SuperChis配置常量
MAGIC_ADDRESS = 0x01FFFFFE  # 魔术解锁地址
MAGIC_VALUE = 0xA55A        # 魔术解锁值

# Flash元数据常量
ROM_OFF_FLASHMETA = 0x00200000
FLASH_METADATA_SIZE = 0x00200000

class SuperChisDevice:
    """SuperChis设备类 - 线程安全"""
    
    def __init__(self, serial_port):
        """
        初始化设备
        Args:
            serial_port: serial.Serial对象
        """
        self.ser = serial_port
    
    def writeRom(self, addr_word, dat):
        """向ROM地址写入数据"""
        if isinstance(dat, int):
            dat = struct.pack("<H", dat)
        
        cmd = []
        cmd.extend(struct.pack("<H", 2 + 1 + 4 + len(dat) + 2))
        cmd.append(0xf5)
        cmd.extend(struct.pack("<I", addr_word))
        cmd.extend(dat)
        cmd.extend([0, 0])
        
        self.ser.write(cmd)
        ack = self.ser.read(1)
        return ack
    
    def readRom(self, addr_word, length_byte):
        """从ROM地址读取数据"""
        cmd = []
        cmd.extend(struct.pack("<H", 2 + 1 + 4 + 2 + 2))
        cmd.append(0xf6)
        cmd.extend(struct.pack("<I", addr_word << 1))
        cmd.extend(struct.pack("<H", length_byte))
        cmd.extend([0, 0])
        
        self.ser.write(cmd)
        respon = self.ser.read(length_byte + 2)
        return respon[2:]
    
    def writeRam(self, addr, dat):
        """向RAM地址写入数据"""
        if isinstance(dat, int):
            dat = struct.pack("B", dat)
        
        cmd = []
        cmd.extend(struct.pack("<H", 2 + 1 + 4 + len(dat) + 2))
        cmd.append(0xf7)
        cmd.extend(struct.pack("<I", addr))
        cmd.extend(dat)
        cmd.extend([0, 0])
        
        self.ser.write(cmd)
        ack = self.ser.read(1)
        return ack
    
    def readRam(self, addr, length_byte):
        """从RAM地址读取数据"""
        cmd = []
        cmd.extend(struct.pack("<H", 2 + 1 + 4 + 2 + 2))
        cmd.append(0xf8)
        cmd.extend(struct.pack("<I", addr))
        cmd.extend(struct.pack("<H", length_byte))
        cmd.extend([0, 0])
        
        self.ser.write(cmd)
        respon = self.ser.read(length_byte + 2)
        return respon[2:]
    
    def set_sc_mode(self, sdram, sd_enable, write_enable, sram_bank=0, ctrl=0x8):
        """执行SuperChis解锁序列"""
        magic_addr_word = MAGIC_ADDRESS >> 1
        config1 = (ctrl << 4) | (sdram | (sd_enable << 1) | (write_enable << 2) | (sram_bank << 3))
        
        self.writeRom(magic_addr_word, MAGIC_VALUE)
        self.writeRom(magic_addr_word, MAGIC_VALUE)
        self.writeRom(magic_addr_word, config1)
        self.writeRom(magic_addr_word, config1)
        return True
    
    def sram_bank_select(self, bank):
        """选择SRAM Bank"""
        # 兼容D卡bank切换
        self.writeRom(0x800000, bank)
        return self.set_sc_mode(sdram=0, sd_enable=0, write_enable=0, sram_bank=bank, ctrl=0xF^(1<<3))
    
    def set_flashmapping(self, mapping):
        """设置Flash映射"""
        if len(mapping) != 8:
            raise ValueError("Mapping must be a list of 8 integers.")
        
        magic_addr_word = MAGIC_ADDRESS >> 1
        MAGIC_FLASH_MAP_VALUE = 0xA558
        
        for i in range(8):
            self.writeRom(magic_addr_word, MAGIC_FLASH_MAP_VALUE)
            self.writeRom(magic_addr_word, MAGIC_FLASH_MAP_VALUE)
            self.writeRom(magic_addr_word, mapping[i])
            self.writeRom(magic_addr_word, mapping[i])
        
        return True
    
    def readRomID(self):
        """读取ROM Flash ID"""
        self.writeRom(0x000555, 0xaa)
        self.writeRom(0x0002aa, 0x55)
        self.writeRom(0x000555, 0x90)
        
        id_data = {}
        id_data['manufacturer'] = struct.unpack("<H", self.readRom(0x00, 2))[0]
        id_data['device_id1'] = struct.unpack("<H", self.readRom(0x01, 2))[0]
        id_data['device_id2'] = struct.unpack("<H", self.readRom(0x0e, 2))[0]
        id_data['device_id3'] = struct.unpack("<H", self.readRom(0x0f, 2))[0]
        
        self.writeRom(0x000000, 0xf0)
        return id_data
    
    def getRomCFI(self):
        """获取ROM CFI信息"""
        self.writeRom(0x55, 0x98)
        cfi = self.readRom(0x27, 20)
        self.writeRom(0x00, 0xf0)
        
        temp = struct.unpack("<H", cfi[0:2])[0]
        device_size = 2 ** temp
        
        temp = struct.unpack("<H", cfi[6:8])[0]
        buffer_write_bytes = 2 ** temp if temp != 0 else 0
        
        temp = struct.unpack("<H", cfi[12:14])[0]
        temp1 = struct.unpack("<H", cfi[14:16])[0]
        sector_count = (((temp1 & 0xff) << 8) | (temp & 0xff)) + 1
        
        temp = struct.unpack("<H", cfi[16:18])[0]
        temp1 = struct.unpack("<H", cfi[18:20])[0]
        sector_size = (((temp1 & 0xff) << 8) | (temp & 0xff)) * 256
        
        return {
            'device_size': device_size,
            'sector_count': sector_count,
            'sector_size': sector_size,
            'buffer_write_bytes': buffer_write_bytes
        }
    
    def getRomEraseTime(self):
        """获取ROM擦除时间（毫秒）"""
        cfi_info = self.getRomCFI()
        
        self.writeRom(0x55, 0x98)
        cfi = self.readRom(0x1f, 20)
        self.writeRom(0x00, 0xf0)
        
        temp = struct.unpack("<H", cfi[4:6])[0]
        timeout_block = 2 ** temp
        
        temp = struct.unpack("<H", cfi[6:8])[0]
        timeout_chip = 2 ** temp
        
        if timeout_chip == 1:
            return timeout_block * cfi_info['sector_count']
        else:
            return timeout_chip
    
    def eraseChip(self):
        """擦除整个芯片"""
        self.writeRom(0x000555, 0xaa)
        self.writeRom(0x0002aa, 0x55)
        self.writeRom(0x000555, 0x80)
        self.writeRom(0x000555, 0xaa)
        self.writeRom(0x0002aa, 0x55)
        self.writeRom(0x000555, 0x10)
        
        time.sleep(0.1)
        while True:
            data = self.readRom(0x000000, 2)
            if struct.unpack("<H", data)[0] == 0xffff:
                break
            time.sleep(0.5)
        
        return True
    
    def eraseSector(self, addr_word, sector_size_words):
        """擦除指定扇区"""
        self.writeRom(0x000555, 0xaa)
        self.writeRom(0x0002aa, 0x55)
        self.writeRom(0x000555, 0x80)
        self.writeRom(0x000555, 0xaa)
        self.writeRom(0x0002aa, 0x55)
        self.writeRom(addr_word, 0x30)
        
        time.sleep(0.01)
        while True:
            data = self.readRom(addr_word, 2)
            if struct.unpack("<H", data)[0] == 0xffff:
                break
            time.sleep(0.1)
        
        return True
    
    def programRom(self, addr_byte, data, buffer_write_bytes=None):
        """
        写入ROM数据（使用固件0xf4命令）
        
        Args:
            addr_byte: 字节地址（注意：0xf4命令使用字节地址，不是字地址）
            data: 要写入的数据
            buffer_write_bytes: Buffer Write大小（如果为None则自动查询CFI）
        """
        if isinstance(data, int):
            data = struct.pack("<H", data)
        
        # 如果没有提供buffer_write_bytes，则查询CFI
        if buffer_write_bytes is None:
            cfi_info = self.getRomCFI()
            buffer_write_bytes = cfi_info['buffer_write_bytes']
        
        # 构造数据包
        cmd = []
        cmd.extend(struct.pack("<H", 2 + 1 + 4 + 2 + len(data) + 2))  # 包大小
        cmd.append(0xf4)  # rom program cmd
        cmd.extend(struct.pack("<I", addr_byte))  # base address (字节地址)
        cmd.extend(struct.pack("<H", buffer_write_bytes))  # bufferWriteBytes
        cmd.extend(data)  # data
        cmd.extend([0, 0])  # CRC占位
        
        self.ser.write(cmd)
        ack = self.ser.read(1)
        
        return ack == b'\xaa'
    
    def unlockPPB(self):
        """解锁所有扇区的PPB保护"""
        self.set_flashmapping([0, 1, 2, 3, 4, 5, 6, 7])
        
        self.writeRom(0x000555, 0xaa)
        self.writeRom(0x0002aa, 0x55)
        self.writeRom(0x000555, 0xc0)
        
        self.writeRom(0, 0x80)
        self.writeRom(0, 0x30)
        
        time.sleep(0.1)
        while True:
            self.writeRom(0x000555, 0x70)
            status = self.readRom(0, 2)
            if struct.unpack("<H", status)[0] & 0x80:
                break
            time.sleep(0.1)
        
        self.writeRom(0, 0x90)
        self.writeRom(0, 0x00)
        self.writeRom(0x000000, 0xf0)
        
        return True
    
    def checkPPBLocked(self):
        """检查PPB是否被锁定"""
        self.writeRom(0x000555, 0xaa)
        self.writeRom(0x0002aa, 0x55)
        self.writeRom(0x000555, 0x50)
        
        status = self.readRom(0, 2)
        lock_status = struct.unpack("<H", status)[0]
        
        self.writeRom(0, 0x90)
        self.writeRom(0, 0x00)
        self.writeRom(0, 0xf0)
        
        return lock_status != 0
    
    def verifyRom(self, addr_word, expected_data):
        """验证ROM数据"""
        actual_data = self.readRom(addr_word, len(expected_data))
        return actual_data == expected_data
    
    def eraseFlashMetadata(self):
        """擦除Flash元数据常量区域 - 重置NOR游戏功能"""
        self.set_flashmapping([0, 1, 2, 3, 4, 5, 6, 7])
        # Flash元数据区域起始地址和大小
        meta_start = ROM_OFF_FLASHMETA  # 0x00200000
        meta_size = FLASH_METADATA_SIZE  # 0x00200000 (2MB)
        
        # 获取CFI信息
        cfi_info = self.getRomCFI()
        sector_size = cfi_info['sector_size']
        
        # 计算需要擦除的扇区数量
        sector_count = meta_size // sector_size
        
        # 擦除所有元数据区域的扇区
        for i in range(sector_count):
            addr_byte = meta_start + i * sector_size
            addr_word = addr_byte >> 1
            self.eraseSector(addr_word, sector_size >> 1)
        
        return True
