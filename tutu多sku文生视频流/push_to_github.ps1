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

$ErrorActionPreference = 'Stop'

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

Write-Host "=== 复制文件（排除 outputs/ __pycache__/）==="
# robocopy 成功返回 1-7 都算正常；8+ 才是错误
robocopy $SRC $target /E /XD outputs __pycache__ /XF *.pyc | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with code $LASTEXITCODE"
}

# 写 .gitignore 防止以后误推 outputs
$gitignore = @"
outputs/
__pycache__/
*.pyc
.DS_Store
"@
Set-Content -Path (Join-Path $target ".gitignore") -Value $gitignore -Encoding utf8

Write-Host "=== git status ==="
git add -A
git status --short

Write-Host "=== 配置用户信息（如未配置）==="
git config user.email "bot@draw-your-dream.com"
git config user.name "draw-your-dream-bot"

Write-Host "=== 创建 commit ==="
$commitMsg = @"
add TUTU multi-SKU 5s text-to-video pipeline

- Phase A: 50-条 A1 context 生成 (Gemini)
- Phase B: 随机 1/7 SKU + 3 张参考图路径 + 派生标题（无 LLM）
- Phase C: 5 秒单镜头 T2V prompt 生成 (Gemini)
- Phase D: Ark Seedance 提交 + 轮询 + 下载（并行版 phase_d_parallel.py 快 3 倍）

尺度控制用周围物体比例对比替代抽象指令；图片3 仅作嘴巴解剖参考不强制张嘴
"@
git commit -m "$commitMsg"

Write-Host "=== 推送 origin main ==="
git push origin main

Write-Host ""
Write-Host "=== 完成 ==="
Write-Host "仓库：https://github.com/draw-your-dream/seedance_agent/tree/main/$TARGET_DIR_NAME"
Write-Host "临时目录（可删）: $TMP"
