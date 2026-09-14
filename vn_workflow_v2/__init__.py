"""vn_workflow_v2：探索解谜玩法的工作流。

和 vn_workflow（分支剧情）并排的第二套生成端：

    lint   用浏览器里跑的那份引擎（frontend/explore/engine.js）做 validate + solve
    art    背景 / CG 生图——直接复用 vn_workflow 第 3 段，同一套素材管线
    build  打包单文件页——和第 6 段同一个页面壳、同一套素材编码，内联的是探索引擎

剧本不是 ink 编译来的，是编剧直接写的 vn/stories/<名字>/story.json（写着 "engine": "explore"）。
产物同样落在 vn/stories/<名字>/，宿主 WebUI、调试面板、静态站两种玩法一起认。
"""
