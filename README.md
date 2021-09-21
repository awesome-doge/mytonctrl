重要連結：
* [gpu-usage-monitoring-cuda](https://unix.stackexchange.com/questions/38560/gpu-usage-monitoring-cuda)
* [nvitop, an interactive NVIDIA-GPU process viewer, the one-stop solution for GPU process management](https://pythonrepo.com/repo/XuehaiPan-nvitop-python-data-validation)
* [awesome-doge/mytonctrl](https://github.com/awesome-doge/mytonctrl)
* [akifoq/mytonctrl](https://github.com/akifoq/mytonctrl)
* [awesome-doge/pow-miner-gpu](https://github.com/awesome-doge/pow-miner-gpu)
* [tontechio/pow-miner-gpu](https://github.com/tontechio/pow-miner-gpu)


# 檢查 ubuntu 系統版本
```
lsb_release -a
```
# cuda 安裝
## cuda  安裝 [ubuntu 18.04]
```
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu1804/x86_64/cuda-ubuntu1804.pin
sudo mv cuda-ubuntu1804.pin /etc/apt/preferences.d/cuda-repository-pin-600
sudo apt-key adv --fetch-keys https://developer.download.nvidia.com/compute/cuda/repos/ubuntu1804/x86_64/7fa2af80.pub
sudo add-apt-repository "deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu1804/x86_64/ /"
sudo apt-get update
sudo apt-get -y install cuda
```

## cuda  安裝 [ubuntu 20.04]
```
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/cuda-ubuntu2004.pin
sudo mv cuda-ubuntu2004.pin /etc/apt/preferences.d/cuda-repository-pin-600
sudo apt-key adv --fetch-keys https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/7fa2af80.pub
sudo add-apt-repository "deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/ /"
sudo apt-get update
sudo apt-get -y install cuda
```

## opencl
```

```

# 安裝 mytonctrl (awesome-doge)
```
wget https://raw.githubusercontent.com/awesome-doge/mytonctrl/master/scripts/install.sh
sudo bash install.sh -m lite
```

# 挖礦測試
```
sudo /usr/bin/ton/crypto/pow-miner-cuda \
 -vv -g0 -w128 -t60 \
 kf_kUHS5Q8lQXb7O-3tmLtxNcwpDIhFDwaTc84vlyb6lW1GW \
 73827319181378257785669135550996526874 \
 98803219716989094792607614989188637103507042933057999722300066529 \
 100000000000 \
 kf-kkdY_B7p-77TLn2hUhM6QidWrrsl8FYWCIvBMpZKprBtN \
 mined.boc
```

# 顯卡測試

> * [nvitop](https://pythonrepo.com/repo/XuehaiPan-nvitop-python-data-validation)
```
nvidia-smi

sudo pip3 install --upgrade nvitop
nvitop -m
```

# screen
```
screen -dmS python3 /root/ton-miner-cuda/ton_miner_groomed/miner.py --config /root/ton-miner-cuda/config/po/cuda7.json

sudo killall screen
```

# 測試結果

```
NVIDIA GeForce GTX 1660 SUPER
[ hashes computed: 38386270208 ]
[ speed: 6.33839e+08 hps ]

NVIDIA GeForce RTX 3060 Ti
[ hashes computed: 68383932416 ]
[ speed: 1.13887e+09 hps ]

NVIDIA GeForce RTX 2080 Ti
[ hashes computed: 110528299008 ]
[ speed: 1.86213e+09 hps ]

NVIDIA Tesla T4
[ hashes computed: 46674214912 ]
[ speed: 7.86217e+08 hps ]

NVIDIA GeForce GTX 1080 Ti
[ hashes computed: 55163486208 ]
[ speed: 9.21737e+08 hps ]

NVIDIA GeForce RTX 3080 Ti
[ hashes computed: 139955535872 ]
[ speed: 2.32585e+09 hps ]

NVIDIA GeForce RTX 3090
[ hashes computed: 100025761792 ]
[ speed: 2.38598e+09 hps ]

Tesla V100-SXM2-16GB
[ hashes computed: 111031615488 ]
[ speed: 1.84561e+09 hps ]

[-w32]
[ hashes computed: 111937585152 ]
[ speed: 1.85526e+09 hps ]

[-w64]
[ hashes computed: 111300050944 ]
[ speed: 1.83681e+09 hps ]

[-w128]
[ hashes computed: 110394081280 ]
[ speed: 1.80021e+09 hps ]
```



