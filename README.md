# bilibili-cheese-downloader 哔哩哔哩课程下载器
## 哔哩哔哩课程下载器 / bilibili cheeses downloader 下载你已购买的或免费的 bilibili 课程。  
### 使用方式：
1. 下载 bdownloader.py 或 release 中的 exe 文件后运行即可。
2. 手机 app 扫码完成登录，随后输入你已拥有的课程 ID 即可完成下载操作。  
注意：通过 ffmpeg 进行音视频合成操作，所以请提前配置好环境变量或将其复制到主程序同级目录下。  
 
### 所需依赖：
python==3.9  
pip install tqdm bilibili-api-python aiohttp 
基本是 ai 搓的，所以问题有很多，有空再改改。
点个 star 谢谢喵，爱你喵。

### 更新：
bdownloader_3.0.py 更新了帧率转换，编码转换，能极大压缩文件体积大小；同时优化了日志，增强了代码健壮性。（编码转换耗时较长，请考虑充分）

### 登录说明（新增）
主流程为 **扫码登录 + 登录态持久化**，首次扫码后自动保存至 `credential.json`，之后启动免扫码，cookie 到期自动续期。

另提供 `access_key_bridge.py` 作为**备用凭证源**：若你已有 logintool 产出的 `access_key`，可保存为 `access_key.json` 后由程序自动兑换为 Credential。该兑换链路非官方、随时可能失效，兑换失败会自动回退扫码登录，**请勿依赖它作为主登录方式**。

凭证文件均需进 `.gitignore`，禁止硬编码进代码或提交 git。