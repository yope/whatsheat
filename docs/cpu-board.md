
# Whatsminer M31S 72TH/s

## CPU board

 - Model: CB4_V10
 - SoC: Allwinner H6
 - NAND: 2Gb NAND flash, 256MiB, 227MiB usable?, type: Kioxia TC58NVG1S3HTAI0
   - ??: MBR, 32768, 16MiB
   - nanda: boot-resource, 16384, 8MiB
   - nandb: env, 32768, 16MiB
   - nandc: boot, 65536, 32MiB
   - nandd: rootfs, 131072, 64MiB
   - nande: recovery, 65536, 32MiB
   - nandf: data, 32768, 16MiB
   - nandg: data-bak, 32768, 16MiB
   - nandh: reserved, 32768, 16MiB
   - nandi: UDISK, 22528, 11MiB
 - RAM: 256MiB
 - POR/supervisor: SGM706-SYS8 (2.93V brownout).



 Kernel command-line: earlyprintk=sunxi-uart,0x05000000 initcall_debug=0 console=ttyS0,115200 loglevel=8 root=/dev/system init=/init isolcpus=1,2,3 partitions=boot-resource@nanda:env@nandb:boot@nandc:rootfs@nandd:recovery@nande:data@nandf:data_bak@nandg:reserved@nandh:UDISK@nandi cma=400M mac_addr=c8:01:26:00:3b:82 wifi_mac= bt_mac= selinux=0 specialstr= hw_version=10 boot_type=0

