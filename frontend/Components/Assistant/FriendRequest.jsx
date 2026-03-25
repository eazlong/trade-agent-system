import React, { useState, useEffect } from 'react';
import { 
  getFriendRequests, 
  getReceivedFriendRequests, 
  sendFriendRequest,
  acceptFriendRequest,
  rejectFriendRequest
} from 'services/social.service';
import { useToasts } from "react-toast-notifications";

const FriendRequest = () => {
  const [receivedRequests, setReceivedRequests] = useState([]);
  const [sentRequests, setSentRequests] = useState([]);
  const [inviteCode, setInviteCode] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState('received'); // 'received' or 'sent'
  const { addToast } = useToasts();

  const [username, setUsername] = useState('');
  useEffect(() => {
    setUsername(localStorage.getItem('username'));
  }, []);
  
  useEffect(() => {
    fetchFriendRequests();
  }, []);

  const fetchFriendRequests = async () => {
    try {
      setLoading(true);
      const response = await getFriendRequests();
      
      // 分类处理请求
      const received = [];
      const sent = [];
      
      response.data.forEach(request => {
        if (request.receiver_info && request.receiver_info.id === request.receiver) {
          received.push(request);
        } else {
          sent.push(request);
        }
      });
      
      setReceivedRequests(received);
      setSentRequests(sent);
    } catch (error) {
      console.error('获取好友请求失败', error);
      addToast("获取好友请求失败", { appearance: "error", autoDismiss: true });
    } finally {
      setLoading(false);
    }
  };

  const handleSendRequest = async (e) => {
    e.preventDefault();
    
    if (!inviteCode.trim()) {
      addToast("请输入邀请码", { appearance: "error", autoDismiss: true });
      return;
    }
    
    try {
      setLoading(true);
      await sendFriendRequest({
        invite_code: inviteCode.trim(),
        message: message.trim()
      });
      
      addToast("好友请求已发送", { appearance: "success", autoDismiss: true });
      setInviteCode('');
      setMessage('');
      
      // 重新获取好友请求列表
      await fetchFriendRequests();
    } catch (error) {
      console.error('发送好友请求失败', error);
      if (error.response && error.response.data) {
        addToast(error.response.data.detail || "发送好友请求失败", { appearance: "error", autoDismiss: true });
      } else {
        addToast("发送好友请求失败", { appearance: "error", autoDismiss: true });
      }
    } finally {
      setLoading(false);
    }
  };

  const handleAccept = async (id) => {
    try {
      setLoading(true);
      await acceptFriendRequest(id);
      addToast("已接受好友请求", { appearance: "success", autoDismiss: true });
      
      // 更新好友请求列表
      setReceivedRequests(receivedRequests.map(req => 
        req.id === id ? { ...req, status: 'accepted' } : req
      ));
    } catch (error) {
      console.error('接受好友请求失败', error);
      addToast("接受好友请求失败", { appearance: "error", autoDismiss: true });
    } finally {
      setLoading(false);
    }
  };

  const handleReject = async (id) => {
    try {
      setLoading(true);
      await rejectFriendRequest(id);
      addToast("已拒绝好友请求", { appearance: "success", autoDismiss: true });
      
      // 更新好友请求列表
      setReceivedRequests(receivedRequests.map(req => 
        req.id === id ? { ...req, status: 'rejected' } : req
      ));
    } catch (error) {
      console.error('拒绝好友请求失败', error);
      addToast("拒绝好友请求失败", { appearance: "error", autoDismiss: true });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-purple-100 shadow-md rounded-lg p-5">
      <h2 className="text-xl font-semibold text-gray-800 mb-5">好友请求</h2>

      {/* 发送好友请求表单 */}
      <div className="mb-6">
        <h3 className="text-lg font-medium text-gray-700 mb-3">添加好友</h3>
        <form onSubmit={handleSendRequest} className="space-y-3">
          <div>
            <label
              htmlFor="inviteCode"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              好友邀请码
            </label>
            <input
              id="inviteCode"
              type="text"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
              placeholder="输入好友的邀请码"
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500"
              disabled={loading}
            />
          </div>

          <div>
            <label
              htmlFor="message"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              留言消息 (选填)
            </label>
            <textarea
              id="message"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="请输入留言消息..."
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500"
              rows={2}
              disabled={loading}
            ></textarea>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full px-4 py-2 bg-purple-600 text-white rounded hover:bg-purple-700 transition-colors duration-300 disabled:bg-purple-300"
          >
            {loading ? "处理中..." : "发送好友请求"}
          </button>
        </form>
      </div>

      {/* 好友请求列表 */}
      <div className="border-t pt-5">
        <div className="flex border-b mb-4">
          <button
            onClick={() => setTab("received")}
            className={`px-4 py-2 ${
              tab === "received"
                ? "border-b-2 border-purple-500 text-purple-600"
                : "text-gray-600"
            }`}
          >
            收到的请求
            {receivedRequests.filter((req) => req.status === "pending").length >
              0 && (
              <span className="ml-2 bg-red-500 text-white rounded-full text-xs px-2 py-1">
                {
                  receivedRequests.filter((req) => req.status === "pending")
                    .length
                }
              </span>
            )}
          </button>
          <button
            onClick={() => setTab("sent")}
            className={`px-4 py-2 ${
              tab === "sent"
                ? "border-b-2 border-purple-500 text-purple-600"
                : "text-gray-600"
            }`}
          >
            发送的请求
          </button>
        </div>

        {loading &&
        receivedRequests.length === 0 &&
        sentRequests.length === 0 ? (
          <div className="text-center py-8">
            <div className="animate-spin inline-block w-8 h-8 border-4 border-gray-300 border-t-purple-600 rounded-full"></div>
            <p className="mt-2 text-gray-600">加载中...</p>
          </div>
        ) : tab === "received" ? (
          receivedRequests.length > 0 ? (
            <div className="space-y-4">
              {receivedRequests.map((request) => (
                <div key={request.id} className="border rounded-lg p-4">
                  <div className="flex justify-between items-start">
                    <div>
                      <h4 className="font-medium">
                        {request.sender_info?.username || "未知用户"}
                      </h4>
                      <p className="text-sm text-gray-600 mt-1">
                        {request.message || "没有留言"}
                      </p>
                      <div className="mt-2 text-xs text-gray-500">
                        请求时间:{" "}
                        {new Date(request.created_at).toLocaleString()}
                      </div>
                    </div>
                    {request.sender_info?.username != username && (
                    <div className="flex items-center">
                      {request.status === "pending" ? (
                        <>
                          <button
                            onClick={() => handleAccept(request.id)}
                            disabled={loading}
                            className="px-3 py-1 bg-green-600 text-white rounded mr-2 hover:bg-green-700 disabled:bg-green-300"
                          >
                            接受
                          </button>
                          <button
                            onClick={() => handleReject(request.id)}
                            disabled={loading}
                            className="px-3 py-1 bg-red-600 text-white rounded hover:bg-red-700 disabled:bg-red-300"
                          >
                            拒绝
                          </button>
                        </>
                      ) : (
                        <span
                          className={`px-2 py-1 rounded text-xs ${
                            request.status === "accepted"
                              ? "bg-green-100 text-green-800"
                              : "bg-red-100 text-red-800"
                          }`}
                        >
                          {request.status === "accepted" ? "已接受" : "已拒绝"}
                        </span>
                      )}
                    </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-gray-500">
              暂无收到的好友请求
            </div>
          )
        ) : sentRequests.length > 0 ? (
          <div className="space-y-4">
            {sentRequests.map((request) => (
              <div key={request.id} className="border rounded-lg p-4">
                <div className="flex justify-between items-start">
                  <div>
                    <h4 className="font-medium">
                      发送给: {request.receiver_info?.username || "未知用户"}
                    </h4>
                    <p className="text-sm text-gray-600 mt-1">
                      {request.message || "没有留言"}
                    </p>
                    <div className="mt-2 text-xs text-gray-500">
                      请求时间: {new Date(request.created_at).toLocaleString()}
                    </div>
                  </div>
                  <div>
                    <span
                      className={`px-2 py-1 rounded text-xs ${
                        request.status === "pending"
                          ? "bg-yellow-100 text-yellow-800"
                          : request.status === "accepted"
                          ? "bg-green-100 text-green-800"
                          : "bg-red-100 text-red-800"
                      }`}
                    >
                      {request.status === "pending"
                        ? "待处理"
                        : request.status === "accepted"
                        ? "已接受"
                        : "已拒绝"}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-8 text-gray-500">
            暂无发送的好友请求
          </div>
        )}
      </div>
    </div>
  );
};

export default FriendRequest; 