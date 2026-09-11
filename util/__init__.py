"""与业务无关的 transport 和工具，三层都可以引用。

    llm_api      文本（deepseek，OpenAI 兼容）
    image_api    生图（火山引擎方舟 Seedream）
    video_api    视频（火山引擎方舟 Seedance，异步任务制）
    audio_synth  占位音轨的程序化合成（只用标准库）
    paths        vn/stories/ 的目录约定

放在根目录而不是 vn_workflow/ 下，是为了让 backend 也能用同一套约定，
同时不必 import 生成端的任何一段业务逻辑。
"""
