import React, { useState, useEffect } from 'react';
import { getUserInvite, regenerateInvite } from 'services/social.service';
import { useToasts } from "react-toast-notifications";

const InviteCode = () => {
  const [invite, setInvite] = useState(null);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);
  const { addToast } = useToasts();   
  useEffect(() => {
    fetchInviteCode();
  }, []);

  const fetchInviteCode = async () => {
    try {
      setLoading(true);
      const response = await getUserInvite();
      setInvite(response.data);
    } catch (error) {
      console.error('获取邀请码失败', error);
      addToast("获取邀请码失败", { appearance: "error", autoDismiss: true });
    } finally {
      setLoading(false);
    }
  };

  const handleRegenerate = async () => {
    try {
      setRegenerating(true);
      const response = await regenerateInvite();
      setInvite(response.data);
      addToast("邀请码已重新生成", { appearance: "success", autoDismiss: true });
    } catch (error) {
      console.error('重新生成邀请码失败', error);
      addToast("重新生成邀请码失败", { appearance: "error", autoDismiss: true });
    } finally {
      setRegenerating(false);
    }
  };

  const handleCopy = () => {
    if (invite?.invite_code) {
      navigator.clipboard.writeText(invite.invite_code);
      addToast("邀请码已复制到剪贴板", { appearance: "success", autoDismiss: true });
    }
  };

  return (
    <div className="bg-purple-100 shadow-md rounded-lg p-5">
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold text-gray-800">我的邀请码</h2>
        <button 
          onClick={handleRegenerate}
          disabled={regenerating}
          className="px-4 py-2 bg-purple-600 text-white rounded hover:bg-purple-700 transition-colors duration-300 disabled:bg-gray-400"
        >
          {regenerating ? '生成中...' : '重新生成'}
        </button>
      </div>

      <div className="flex items-center justify-between border rounded-lg p-3 bg-gray-50">
        {loading ? (
          <div className="animate-pulse h-6 w-32 bg-gray-300 rounded"></div>
        ) : (
          <div className="font-mono text-lg font-semibold">{invite?.invite_code}</div>
        )}
        
        <button 
          onClick={handleCopy}
          disabled={loading || !invite}
          className="px-3 py-1 bg-gray-200 text-gray-800 rounded hover:bg-gray-300 transition-colors duration-300 disabled:bg-gray-100 disabled:text-gray-400"
        >
          复制
        </button>
      </div>
      
      <p className="mt-3 text-sm text-gray-600">
        将此邀请码分享给你的好友，他们可以通过此邀请码向你发送好友请求。
      </p>
    </div>
  );
};

export default InviteCode; 