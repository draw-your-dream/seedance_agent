# 一键推送 tutu多sku文生视频流 到 draw-your-dream/seedance_agent main
# 用法：
#   1. 打开 PowerShell
#   2. cd 到本目录
#   3. .\push_to_github.ps1
#
# 做的事：
#   - 克隆 main 到临时目录
#   - 作为并列子目录 tutu多sku文生视频流/ 放进去
#   - 排除 outputs/ 和 __pycache__/（通过 .gitignore）
#   - git commit + git push origin main
#
# ⚠️ 第一次运行前：请到 https://github.com/settings/tokens
#    重新生成一个 token（之前那个已在聊天里暴露，建议立刻 revoke）
#    然后把新 token 粘到下面 $TOKEN，或运行时交互输入

$ErrorActionPreference = 'Continue'  # git/robocopy 把进度写到 stderr，不能用 Stop
$PSNativeCommandUseErrorActionPreference = $false

$TOKEN = $env:GITHUB_TOKEN
if (-not $TOKEN) {
    $TOKEN = Read-Host -AsSecureString "请粘贴 GitHub Personal Access Token (输入隐藏)"
    $TOKEN = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($TOKEN))
}

$REPO_URL = "https://x-access-token:$TOKEN@github.com/draw-your-dream/seedance_agent.git"
$TARGET_DIR_NAME = "tutu多sku文生视频流"
$SRC = "F:\workspace\tutu内容\tutu多sku文生视频流"

$TMP = Join-Path $env:TEMP ("seedance_push_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
Write-Host "=== 临时工作目录: $TMP ==="
New-Item -ItemType Directory -Path $TMP -Force | Out-Null
Set-Location $TMP

Write-Host "=== 克隆 main ==="
git clone --branch main --single-branch "$REPO_URL" repo
Set-Location repo

# 如果目标目录已存在则先清掉（这样每次推送是一个干净的 overwrite）
$target = Join-Path (Get-Location) $TARGET_DIR_NAME
if (Test-Path $target) {
    Write-Host "=== 目标已存在，先删除再重新同步 ==="
    Remove-Item -Recurse -Force $target
}

Write-Host "=== 复制文件（排除 outputs/ __pycache__/ 历史快照 md）==="
# robocopy 成功返回 1-7 都算正常；8+ 才是错误
# /XD: 排除目录；/XF: 排除文件（含通配符，会排除所有 ABC对齐 开头的 md 和 50条完整样本.md 等历史快照）
robocopy $SRC $target /E /XD outputs __pycache__ /XF *.pyc "ABC对齐*.md" "50条完整样本.md" | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with code $LASTEXITCODE"
}

# 写 .gitignore 防止以后误推 outputs / 历史快照
$gitignore = @"
outputs/
__pycache__/
*.pyc
.DS_Store
ABC对齐*.md
50条完整样本.md
"@
Set-Content -Path (Join-Path $target ".gitignore") -Value $gitignore -Encoding utf8

Write-Host "=== git status ==="
git add -A
git status --short

Write-Host "=== 配置用户信息（如未配置）==="
git config user.email "bot@draw-your-dream.com"
git config user.name "draw-your-dream-bot"

Write-Host "=== 创建 commit ==="
# 把 commit message 写到临时文件（避免命令行多行字符串被 shell 拆解）
$commitMsgFile = Join-Path $TMP "commit_msg.txt"
$commitMsg = @"
update TUTU multi-SKU 5s text-to-video pipeline

- Phase A: A1 context 生成 (Gemini 2.5-flash)，加入跨批次防重复反例 + cosplay/各类职业维度 + 室内/户外/旅途空间发散
- Phase B: 60/40 权重抽 SKU + 4 张参考图（SKU 主图/手脚/表情/屁股）+ sku_name + sku_full_phrase 注入
- Phase C: 5 秒单镜头 T2V prompt 生成 (Gemini 3-flash-preview)，规则收紧：
    * 4 张参考图首段引用 + 显式声明 X款蘑菇TUTU 不能有尾巴
    * 镜头单向平滑、禁手持呼吸感/晃动/抖动、运镜多样化
    * 音效段必含 禁止背景音乐 硬指令
    * 图片2 规范文案统一为 毛绒粉色小手小脚 + 不准是圆柱形肉垫
    * 约束段第三项必须写 参考图片3 嘴形和嘴内颜色
- Phase D: phase_d_parallel.py 流式归档（--package-dir）：预分配 NNNN 编号 + 立即写 txt + mp4 下完即拷
- 新增 package_to_final.py 独立打包脚本

尺度控制用周围物体比例对比替代抽象指令；秃秃自身性格保持不变（cosplay 只是假装做职业）
"@
[System.IO.File]::WriteAllText($commitMsgFile, $commitMsg, (New-Object System.Text.UTF8Encoding $false))
git commit -F $commitMsgFile

Write-Host "=== 推送 origin main ==="
git push origin main

Write-Host ""
Write-Host "=== 完成 ==="
Write-Host "仓库：https://github.com/draw-your-dream/seedance_agent/tree/main/$TARGET_DIR_NAME"
Write-Host "临时目录（可删）: $TMP"
