#!/usr/bin/env python3
# -*- coding: utf_8 -*-

"""
清理代理錢包腳本
"""

import os
import glob

def cleanup_proxy_wallets():
    """清理代理錢包文件"""
    print("開始清理代理錢包...")
    
    # 可能的錢包目錄路徑
    possible_paths = [
        "/home/parallels/.local/share/mytoncore/wallets/",
        "/tmp/mytoncore/wallets/",
        "/root/.local/share/mytoncore/wallets/",
        os.path.expanduser("~/.local/share/mytoncore/wallets/")
    ]
    
    proxy_wallets = ["proxy_wallet1", "proxy_wallet2"]
    
    for wallet_name in proxy_wallets:
        print(f"清理錢包：{wallet_name}")
        
        for base_path in possible_paths:
            if os.path.exists(base_path):
                # 查找所有相關文件
                patterns = [
                    f"{base_path}{wallet_name}.*",
                    f"{base_path}{wallet_name}_*"
                ]
                
                for pattern in patterns:
                    files = glob.glob(pattern)
                    for file_path in files:
                        try:
                            os.remove(file_path)
                            print(f"  已刪除：{file_path}")
                        except Exception as e:
                            print(f"  刪除失敗 {file_path}: {e}")
    
    print("代理錢包清理完成！")

if __name__ == "__main__":
    cleanup_proxy_wallets() 