set -e
# Проверить sudo
if [ "$(id -u)" != "0" ]; then
	echo "Please run script as root"
	exit 1
fi
# 顏色定義
COLOR='\033[92m'
ENDC='\033[0m'
# 安裝 依賴套件
echo -e "${COLOR}[1]${ENDC} 安裝 依賴套件"
apt install -y build-essential git make cmake clang libgflags-dev zlib1g-dev libssl-dev libreadline-dev libmicrohttpd-dev pkg-config libgsl-dev python3 python3-dev python3-pip
pip3 install psutil crc16 requests screen 
# 移除舊的
echo -e "${COLOR}[2]${ENDC} 移除舊的/usr/bin/ton/ /usr/src/ton/"
rm -rf /usr/bin/ton/
rm -rf /usr/src/ton/
rm -rf /usr/src/pow-miner-gpu/
# 下載 pow-miner-gpu
echo -e "${COLOR}[3]${ENDC} 下載 pow-miner-gpu"
cd /usr/src
git clone --recursive https://github.com/awesome-doge/pow-miner-gpu.git
cd /usr/src/pow-miner-gpu
git checkout main
cd /usr/src/
mv pow-miner-gpu ton
# opencl
echo -e "${COLOR}[4]${ENDC} 安裝opencl"
apt-get install -y opencl-headers ocl-icd-libopencl1 ocl-icd-opencl-dev clinfo
# 初始化編譯環境
echo -e "${COLOR}[5]${ENDC} 初始化編譯環境"
mkdir /usr/bin/ton
cd /usr/bin/ton
export CCACHE_DISABLE=1
cmake -DCMAKE_BUILD_TYPE=Release -DMINEROPENCL=true /usr/src/ton
# 計算需要的cpu
memory=$(cat /proc/meminfo | grep MemAvailable | awk '{print $2}')
let "cpuNumber = memory / 2100000"
# 編譯
echo -e "${COLOR}[6]${ENDC} 編譯pow-miner-opencl lite-client"
make -j ${cpuNumber} pow-miner-opencl lite-client fift
cd ~