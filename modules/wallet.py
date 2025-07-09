import base64
import os

from modules.module import MtcModule
from mypylib.mypylib import color_print, print_table


class WalletModule(MtcModule):

    description = ''
    default_value = False

    def create_new_wallet(self, args):
        version = "v1"
        try:
            if len(args) == 0:
                walletName = self.ton.GenerateWalletName()
                workchain = 0
            else:
                workchain = int(args[0])
                walletName = args[1]
            if len(args) > 2:
                version = args[2]
            if len(args) == 4:
                subwallet = int(args[3])
            else:
                subwallet = 698983191 + workchain  # 0x29A9A317 + workchain
        except:
            color_print("{red}Bad args. Usage:{endc} nw <workchain-id> <wallet-name> [<version> <subwallet>]")
            return
        wallet = self.ton.CreateWallet(walletName, workchain, version, subwallet=subwallet)
        table = list()
        table += [["Name", "Workchain", "Address"]]
        table += [[wallet.name, wallet.workchain, wallet.addrB64_init]]
        print_table(table)
    # end define

    def _wallets_check(self):
        self.local.add_log("start WalletsCheck function", "debug")
        wallets = self.get_wallets()
        for wallet in wallets:
            if os.path.isfile(wallet.bocFilePath):
                account = self.ton.GetAccount(wallet.addrB64)
                if account.balance > 0:
                    self.ton.SendFile(wallet.bocFilePath, wallet)
    # end define

    def activate_wallet(self, args):
        try:
            walletName = args[0]
        except Exception as err:
            walletName = "all"
        if walletName == "all":
            self._wallets_check()
        else:
            wallet = self.ton.GetLocalWallet(walletName)
            self.ton.ActivateWallet(wallet)
        color_print("ActivateWallet - {green}OK{endc}")
    # end define

    def get_wallets(self):
        self.local.add_log("start GetWallets function", "debug")
        wallets = list()
        wallets_name_list = self.ton.GetWalletsNameList()
        for walletName in wallets_name_list:
            wallet = self.ton.GetLocalWallet(walletName)
            wallets.append(wallet)
        return wallets
    # end define

    def print_wallets_list(self, args):
        table = list()
        table += [["Name", "Status", "Balance", "Ver", "Wch", "Address"]]
        data = self.get_wallets()
        if data is None or len(data) == 0:
            print("No data")
            return
        for wallet in data:
            account = self.ton.GetAccount(wallet.addrB64)
            if account.status != "active":
                wallet.addrB64 = wallet.addrB64_init
            table += [[wallet.name, account.status, account.balance, wallet.version, wallet.workchain, wallet.addrB64]]
        print_table(table)
    # end define

    def do_import_wallet(self, addr_b64, key):
        addr_bytes = self.ton.addr_b64_to_bytes(addr_b64)
        pk_bytes = base64.b64decode(key)
        wallet_name = self.ton.GenerateWalletName()
        wallet_path = self.ton.walletsDir + wallet_name
        with open(wallet_path + ".addr", 'wb') as file:
            file.write(addr_bytes)
        with open(wallet_path + ".pk", 'wb') as file:
            file.write(pk_bytes)
        return wallet_name
    # end define

    def import_wallet(self, args):
        try:
            addr = args[0]
            key = args[1]
        except:
            color_print("{red}Bad args. Usage:{endc} iw <wallet-addr> <wallet-secret-key>")
            return
        name = self.do_import_wallet(addr, key)
        print("Wallet name:", name)
    # end define

    def set_wallet_version(self, args):
        try:
            addr = args[0]
            version = args[1]
        except:
            color_print("{red}Bad args. Usage:{endc} swv <wallet-addr> <wallet-version>")
            return
        self.ton.SetWalletVersion(addr, version)
        color_print("SetWalletVersion - {green}OK{endc}")
    # end define

    def do_export_wallet(self, wallet_name):
        wallet = self.ton.GetLocalWallet(wallet_name)
        with open(wallet.privFilePath, 'rb') as file:
            data = file.read()
        key = base64.b64encode(data).decode("utf-8")
        return wallet.addrB64, key
    # end define

    def export_wallet(self, args):
        try:
            name = args[0]
        except:
            color_print("{red}Bad args. Usage:{endc} ew <wallet-name>")
            return
        addr, key = self.do_export_wallet(name)
        print("Wallet name:", name)
        print("Address:", addr)
        print("Secret key:", key)
    # end define

    def delete_wallet(self, args):
        try:
            wallet_name = args[0]
        except:
            color_print("{red}Bad args. Usage:{endc} dw <wallet-name>")
            return
        if input("Are you sure you want to delete this wallet (yes/no): ") != "yes":
            print("Cancel wallet deletion")
            return
        wallet = self.ton.GetLocalWallet(wallet_name)
        wallet.Delete()
        color_print("DeleteWallet - {green}OK{endc}")
    # end define

    def move_coins(self, args):
        try:
            wallet_name = args[0]
            destination = args[1]
            amount = args[2]
            flags = args[3:]
        except:
            color_print("{red}Bad args. Usage:{endc} mg <wallet-name> <account-addr | bookmark-name> <amount>")
            return
        wallet = self.ton.GetLocalWallet(wallet_name)
        destination = self.ton.get_destination_addr(destination)
        self.ton.MoveCoins(wallet, destination, amount, flags=flags)
        color_print("MoveCoins - {green}OK{endc}")
    # end define

    def do_move_coins_through_proxy(self, wallet, dest, coins):
        self.local.add_log("start MoveCoinsThroughProxy function", "debug")
        
        # 清理可能存在的舊代理錢包
        self._cleanup_proxy_wallets()
        
        # 創建兩個臨時代理錢包
        wallet1 = self.ton.CreateWallet("proxy_wallet1", 0)
        wallet2 = self.ton.CreateWallet("proxy_wallet2", 0)
        
        # 檢查源錢包餘額
        source_account = self.ton.GetAccount(wallet.addrB64)
        if source_account.balance < float(coins) + 0.1:
            raise Exception(f"源錢包餘額不足。需要 {float(coins) + 0.1} TON，但只有 {source_account.balance} TON")
        
        try:
            # 第一階段：從源錢包轉移到第一個代理錢包
            self.local.add_log("第一階段：轉移到代理錢包1", "debug")
            self.ton.MoveCoins(wallet, wallet1.addrB64_init, coins)
            
            # 等待交易確認
            self.ton.WaitTransaction(wallet)
            
            # 等待一段時間讓區塊鏈狀態更新
            import time
            time.sleep(5)
            
            # 檢查代理錢包1的狀態
            wallet1_account = self.ton.GetAccount(wallet1.addrB64)
            self.local.add_log(f"代理錢包1狀態：{wallet1_account.status}, 餘額：{wallet1_account.balance}", "debug")
            
            if wallet1_account.status == "uninit":
                self.local.add_log("啟動代理錢包1", "debug")
                self.ton.SendFile(wallet1.bocFilePath, wallet1, remove=False)
                self.ton.WaitTransaction(wallet1)
                time.sleep(3)  # 等待初始化完成
            elif wallet1_account.status == "empty":
                # 檢查是否有餘額
                if wallet1_account.balance > 0:
                    self.local.add_log("代理錢包1有餘額但狀態為empty，嘗試初始化", "debug")
                    self.ton.SendFile(wallet1.bocFilePath, wallet1, remove=False)
                    self.ton.WaitTransaction(wallet1)
                    time.sleep(3)
                else:
                    raise Exception("代理錢包1轉移失敗，沒有收到資金")
            
            # 第二階段：從代理錢包1轉移到代理錢包2
            self.local.add_log("第二階段：轉移到代理錢包2", "debug")
            self.ton.MoveCoins(wallet1, wallet2.addrB64_init, "alld")
            
            # 等待交易確認
            self.ton.WaitTransaction(wallet1)
            time.sleep(5)
            
            # 檢查代理錢包2的狀態
            wallet2_account = self.ton.GetAccount(wallet2.addrB64)
            self.local.add_log(f"代理錢包2狀態：{wallet2_account.status}, 餘額：{wallet2_account.balance}", "debug")
            
            if wallet2_account.status == "uninit":
                self.local.add_log("啟動代理錢包2", "debug")
                self.ton.SendFile(wallet2.bocFilePath, wallet2, remove=False)
                self.ton.WaitTransaction(wallet2)
                time.sleep(3)
            elif wallet2_account.status == "empty":
                # 檢查是否有餘額
                if wallet2_account.balance > 0:
                    self.local.add_log("代理錢包2有餘額但狀態為empty，嘗試初始化", "debug")
                    self.ton.SendFile(wallet2.bocFilePath, wallet2, remove=False)
                    self.ton.WaitTransaction(wallet2)
                    time.sleep(3)
                else:
                    raise Exception("代理錢包2轉移失敗，沒有收到資金")
            
            # 第三階段：從代理錢包2轉移到最終目標
            self.local.add_log("第三階段：轉移到最終目標", "debug")
            self.ton.MoveCoins(wallet2, dest, "alld", flags=["-n"])
            
            # 等待最終交易確認
            self.ton.WaitTransaction(wallet2)
            
        except Exception as e:
            self.local.add_log(f"代理轉移過程中發生錯誤: {str(e)}", "error")
            raise e
        finally:
            # 清理臨時錢包
            self._cleanup_proxy_wallets()
    
    def _cleanup_proxy_wallets(self):
        """清理代理錢包"""
        try:
            proxy_wallets = ["proxy_wallet1", "proxy_wallet2"]
            for wallet_name in proxy_wallets:
                try:
                    wallet = self.ton.GetLocalWallet(wallet_name)
                    if wallet:
                        wallet.Delete()
                        self.local.add_log(f"已刪除代理錢包：{wallet_name}", "debug")
                except Exception as e:
                    self.local.add_log(f"刪除代理錢包 {wallet_name} 時發生錯誤: {str(e)}", "warning")
        except Exception as e:
            self.local.add_log(f"清理代理錢包時發生錯誤: {str(e)}", "warning")

    def move_coins_through_proxy(self, args):
        try:
            wallet_name = args[0]
            destination = args[1]
            amount = args[2]
        except:
            color_print("{red}Bad args. Usage:{endc} mgtp <wallet-name> <account-addr | bookmark-name> <amount>")
            return
        
        try:
            # 獲取源錢包
            wallet = self.ton.GetLocalWallet(wallet_name)
            if wallet is None:
                color_print("{red}錯誤：找不到錢包 '{wallet_name}'{endc}")
                return
            
            # 獲取目標地址
            destination = self.ton.get_destination_addr(destination)
            if destination is None:
                color_print("{red}錯誤：無效的目標地址{endc}")
                return
            
            # 驗證金額
            try:
                amount_float = float(amount)
                if amount_float <= 0:
                    color_print("{red}錯誤：金額必須大於 0{endc}")
                    return
            except ValueError:
                color_print("{red}錯誤：無效的金額格式{endc}")
                return
            
            # 執行代理轉移
            self.do_move_coins_through_proxy(wallet, destination, amount)
            color_print("MoveCoinsThroughProxy - {green}OK{endc}")
            
        except Exception as e:
            color_print(f"{{red}}代理轉移失敗：{str(e)}{{endc}}")
            self.local.add_log(f"mgtp error: {str(e)}", "error")
    # end define

    def add_console_commands(self, console):
        console.AddItem("nw", self.create_new_wallet, self.local.translate("nw_cmd"))
        console.AddItem("aw", self.activate_wallet, self.local.translate("aw_cmd"))
        console.AddItem("wl", self.print_wallets_list, self.local.translate("wl_cmd"))
        console.AddItem("iw", self.import_wallet, self.local.translate("iw_cmd"))
        console.AddItem("swv", self.set_wallet_version, self.local.translate("swv_cmd"))
        console.AddItem("ew", self.export_wallet, self.local.translate("ex_cmd"))
        console.AddItem("dw", self.delete_wallet, self.local.translate("dw_cmd"))
        console.AddItem("mg", self.move_coins, self.local.translate("mg_cmd"))
        console.AddItem("mgtp", self.move_coins_through_proxy, self.local.translate("mgtp_cmd"))
