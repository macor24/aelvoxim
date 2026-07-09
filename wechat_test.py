import uiautomation as auto
import json

def get_wechat_info():
    wechat = auto.WindowControl(Name="微信", ClassName="WeChatMainWndForPC")
    if not wechat.Exists():
        return {"error": "微信窗口未找到"}

    # 获取左侧会话列表
    session_list = wechat.ListControl(Name="会话")
    chats = []
    if session_list.Exists():
        for item in session_list.GetChildren():
            chats.append(item.Name)

    # 获取当前聊天消息区域
    msg_area = wechat.ListControl(Name="消息")
    messages = []
    if msg_area.Exists():
        for item in msg_area.GetChildren():
            if item.ControlTypeName == "ListItemControl":
                # 提取消息文本和角色
                text = item.Name
                # 简单判断：如果气泡偏右可能是自己发的
                messages.append({"text": text, "raw": item})

    result = {
        "chat_list": chats[:10],
        "current_messages": messages[-5:]  # 最后5条
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

if __name__ == "__main__":
    get_wechat_info()