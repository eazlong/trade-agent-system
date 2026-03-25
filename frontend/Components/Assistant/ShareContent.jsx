import React, { useState } from 'react';
import { createSharedContent } from 'services/social.service';
import { useToasts } from "react-toast-notifications";

const ShareContent = ({ contentType, contentId, title, onShare, iconSize = 16, showText = true }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [shareTitle, setShareTitle] = useState(title || '');
  const [description, setDescription] = useState('');
  const [isPublic, setIsPublic] = useState(false);
  const [loading, setLoading] = useState(false);
  const { addToast } = useToasts();
  const contentTypeNames = {
    system: '交易系统',
    plan: '交易计划', 
    summary: '交易总结'
  };

  const handleShare = async (e) => {
    e.preventDefault();
    
    if (!shareTitle.trim()) {
      addToast("请输入分享标题", { appearance: "error", autoDismiss: true  });
      return;
    }
    
    try {
      setLoading(true);
      await createSharedContent({
        content_type: contentType,
        content_id: contentId,
        title: shareTitle,
        description: description,
        is_public: isPublic
      });
      
      addToast("内容已成功分享", { appearance: "success", autoDismiss: true });
      setIsOpen(false);
      
      // 回调通知父组件
      if (onShare) {
        onShare();
      }
    } catch (error) {
      console.error('分享内容失败', error);
      if (error.response && error.response.data) {
        addToast(error.response.data.detail || "分享内容失败", { appearance: "error", autoDismiss: true });
      } else {
        addToast("分享内容失败", { appearance: "error", autoDismiss: true });
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <button
        onClick={() => setIsOpen(true)}
        className={`flex items-center text-purple-600 hover:text-purple-800 transition-colors ${showText ? 'text-sm' : ''}`}
        style={{ 
          padding: showText ? '4px 8px' : '4px',
          borderRadius: '4px'
        }}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          style={{ 
            width: `${iconSize}px`, 
            height: `${iconSize}px`,
            marginRight: showText ? '4px' : '0'
          }}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z"
          />
        </svg>
        {showText && '分享'}
      </button>

      {isOpen && (
        <div className="fixed inset-0 bg-gray-600 bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-purple-100 rounded-lg shadow-xl max-w-md w-full mx-4">
            <div className="px-6 py-4 border-b">
              <h3 className="text-lg font-semibold text-gray-800">
                分享{contentTypeNames[contentType] || "内容"}
              </h3>
            </div>

            <form onSubmit={handleShare} className="p-6">
              <div className="mb-4">
                <label
                  htmlFor="title"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  分享标题 <span className="text-red-500">*</span>
                </label>
                <input
                  id="title"
                  type="text"
                  value={shareTitle}
                  onChange={(e) => setShareTitle(e.target.value)}
                  placeholder="输入分享标题"
                  className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-purple-500 focus:border-purple-500"
                  required
                  disabled={loading}
                />
              </div>

              <div className="mb-4">
                <label
                  htmlFor="description"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  描述 (选填)
                </label>
                <textarea
                  id="description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="输入分享描述..."
                  rows={3}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-purple-500 focus:border-purple-500"
                  disabled={loading}
                ></textarea>
              </div>

              <div className="mb-6">
                <div className="flex items-center">
                  <input
                    id="isPublic"
                    type="checkbox"
                    checked={isPublic}
                    onChange={(e) => setIsPublic(e.target.checked)}
                    className="h-4 w-4 text-purple-600 border-gray-300 rounded focus:ring-purple-500"
                    disabled={loading}
                  />
                  <label
                    htmlFor="isPublic"
                    className="ml-2 block text-sm text-gray-700"
                  >
                    公开分享（所有用户可见）
                  </label>
                </div>
                <p className="mt-1 text-xs text-gray-500">
                  如不勾选，则仅对你的好友可见
                </p>
              </div>

              <div className="flex justify-end space-x-3">
                <button
                  type="button"
                  onClick={() => setIsOpen(false)}
                  className="px-4 py-2 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500"
                  disabled={loading}
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={loading}
                  className="px-4 py-2 bg-purple-600 border border-transparent rounded-md shadow-sm text-sm font-medium text-white hover:bg-purple-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 disabled:bg-purple-300"
                >
                  {loading ? "分享中..." : "确认分享"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default ShareContent; 