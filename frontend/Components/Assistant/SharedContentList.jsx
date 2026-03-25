import React, { useState, useEffect } from 'react';
import { 
  getSharedContents, 
  getMySharedContents, 
  getFriendsSharedContents, 
  getPublicSharedContents,
  deleteSharedContent,
  toggleContentLike
} from 'services/social.service';
import ContentComments from './ContentComments';
import { useToasts } from "react-toast-notifications";
import SimpleEditor from './SimpleEditor';

const SharedContentList = () => {
  const [tab, setTab] = useState('all'); // 'all', 'mine', 'friends', 'public'
  const [contents, setContents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showComments, setShowComments] = useState({});
  const [deletingId, setDeletingId] = useState(null);
  const [username, setUsername] = useState(null);
  const [fullscreenContent, setFullscreenContent] = useState(null);
  const { addToast } = useToasts();
  const contentTypeNames = {
    system: '交易系统',
    plan: '交易计划', 
    summary: '交易总结'
  };

  useEffect(() => {
    const username = localStorage.getItem('username');
    setUsername(username);
  }, []);

  useEffect(() => {
    fetchContents(tab);
  }, [tab]);

  const fetchContents = async (tabType) => {
    try {
      setLoading(true);
      let response;
      
      switch (tabType) {
        case 'mine':
          response = await getMySharedContents();
          break;
        case 'friends':
          response = await getFriendsSharedContents();
          break;
        case 'public':
          response = await getPublicSharedContents();
          break;
        default:
          response = await getSharedContents();
      }
      
      setContents(response.data || []);
    } catch (error) {
      console.error('获取分享内容失败', error);
      addToast("获取分享内容失败", { appearance: "error", autoDismiss: true });
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id) => {
    if (!confirm('确定要删除这条分享内容吗？')) {
      return;
    }
    
    try {
      setDeletingId(id);
      await deleteSharedContent(id);
      
      // 更新内容列表
      setContents(contents.filter(content => content.id !== id));
      addToast("分享内容已删除", { appearance: "success", autoDismiss: true });
    } catch (error) {
      console.error('删除分享内容失败', error);
      addToast("删除分享内容失败", { appearance: "error", autoDismiss: true });
    } finally {
      setDeletingId(null);
    }
  };

  const handleLike = async (id) => {
      try {
      await toggleContentLike(id);
      
      // 更新内容列表中的点赞状态
      setContents(contents.map(content => {
        if (content.id === id) {
          return {
            ...content,
            likes_count: content.is_liked ? content.likes_count - 1 : content.likes_count + 1,
            is_liked: !content.is_liked
          };
        }
        return content;
      }));
    } catch (error) {
      console.error('处理点赞失败', error);
      addToast("处理点赞失败", { appearance: "error", autoDismiss: true });
    }
  };

  const toggleComments = (id) => {
    setShowComments({
      ...showComments,
      [id]: !showComments[id]
    });
  };

  const openFullscreen = (content) => {
    setFullscreenContent(content);
  };

  const closeFullscreen = () => {
    setFullscreenContent(null);
  };

  return (
    <div className="bg-purple-100 shadow-md rounded-lg p-5">
      <h2 className="text-xl font-semibold text-gray-800 mb-4">交易内容分享</h2>

      <div className="flex border-b mb-5">
        <button
          onClick={() => setTab("all")}
          className={`px-4 py-2 ${
            tab === "all"
              ? "border-b-2 border-purple-500 text-purple-600"
              : "text-gray-600"
          }`}
        >
          全部
        </button>
        <button
          onClick={() => setTab("mine")}
          className={`px-4 py-2 ${
            tab === "mine"
              ? "border-b-2 border-purple-500 text-purple-600"
              : "text-gray-600"
          }`}
        >
          我的分享
        </button>
        <button
          onClick={() => setTab("friends")}
          className={`px-4 py-2 ${
            tab === "friends"
              ? "border-b-2 border-purple-500 text-purple-600"
              : "text-gray-600"
          }`}
        >
          好友分享
        </button>
        <button
          onClick={() => setTab("public")}
          className={`px-4 py-2 ${
            tab === "public"
              ? "border-b-2 border-purple-500 text-purple-600"
              : "text-gray-600"
          }`}
        >
          公开分享
        </button>
      </div>

      {loading ? (
        <div className="text-center py-8">
          <div className="animate-spin inline-block w-8 h-8 border-4 border-gray-300 border-t-purple-600 rounded-full"></div>
          <p className="mt-2 text-gray-600">加载中...</p>
        </div>
      ) : contents.length > 0 ? (
        <div className="space-y-6">
          {contents.map((content) => (
            <div key={content.id} className="border rounded-lg overflow-hidden">
              <div className="p-4">
                <div className="flex justify-between items-start mb-3">
                  <div>
                    <h3 className="font-semibold text-lg">{content.title}</h3>
                    <div className="flex items-center text-sm text-gray-500 mt-1">
                      <span className="mr-2">
                        分享者: {content.user_info?.username || "未知用户"}
                      </span>
                      <span className="mr-2">|</span>
                      <span className="mr-2">
                        类型: {contentTypeNames[content.content_type] || "未知"}
                      </span>
                      <span className="mr-2">|</span>
                      <span>
                        {new Date(content.created_at).toLocaleString()}
                      </span>
                    </div>
                  </div>

                  {username === content.user_info?.username && (
                    <button
                      onClick={() => handleDelete(content.id)}
                      disabled={deletingId === content.id}
                      className="text-red-600 hover:text-red-800"
                    >
                      {deletingId === content.id ? "取消中..." : "取消分享"}
                    </button>
                  )}
                </div>

                {content.description && (
                  <p className="text-gray-700 mb-3">{content.description}</p>
                )}

                <div className="bg-gray-50 p-3 rounded-md">
                  {/* 简单的内容预览 */}
                  <div className="text-sm text-gray-700">
                    {content.content_detail &&
                      content.content_detail.content && (
                        <div className="prose prose-sm max-h-64 overflow-y-auto">
                          {/* 安全展示HTML内容 */}
                          <SimpleEditor
                            initialContent={
                              content.content_detail.content.content
                            }
                            readOnly={true}
                          />
                        </div>
                      )}
                  </div>
                </div>

                <div className="mt-4 flex items-center justify-between">
                  <div className="flex space-x-4">
                    <button
                      onClick={() => handleLike(content.id)}
                      className={`flex items-center ${
                        content.is_liked ? "text-purple-600" : "text-gray-600"
                      } hover:text-purple-800`}
                    >
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-5 w-5 mr-1"
                        fill={content.is_liked ? "currentColor" : "none"}
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={content.is_liked ? 0 : 1.5}
                          d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"
                        />
                      </svg>
                      <span>{content.likes_count || 0}</span>
                    </button>

                    <button
                      onClick={() => toggleComments(content.id)}
                      className="flex items-center text-gray-600 hover:text-purple-800"
                    >
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-5 w-5 mr-1"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={1.5}
                          d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
                        />
                      </svg>
                      <span>{content.comments_count || 0}</span>
                    </button>

                    <button
                      onClick={() => openFullscreen(content)}
                      className="flex items-center text-gray-600 hover:text-purple-800"
                    >
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-5 w-5 mr-1"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={1.5}
                          d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5v-4m0 4h-4m4 0l-5-5"
                        />
                      </svg>
                      <span>全屏</span>
                    </button>
                  </div>

                  {content.is_public && (
                    <span className="text-xs bg-purple-100 text-purple-800 px-2 py-1 rounded-full">
                      公开分享
                    </span>
                  )}
                </div>
              </div>

              {/* 评论区域 */}
              {showComments[content.id] && (
                <div className="border-t bg-gray-50 p-4">
                  <ContentComments contentId={content.id} />
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-8 text-gray-500">暂无分享内容</div>
      )}

      {/* 全屏展示模态框 */}
      {fullscreenContent && (
        <div className="fixed inset-0 bg-black bg-opacity-75 z-50 flex items-center justify-center">
          <div className="bg-white w-full h-full md:w-4/5 md:h-5/6 md:rounded-lg flex flex-col overflow-hidden">
            <div className="bg-purple-600 text-white p-4 flex justify-between items-center">
              <div>
                <h2 className="text-xl font-semibold">{fullscreenContent.title}</h2>
                <div className="text-sm opacity-90 mt-1">
                  <span className="mr-2">
                    分享者: {fullscreenContent.user_info?.username || "未知用户"}
                  </span>
                  <span className="mr-2">|</span>
                  <span className="mr-2">
                    类型: {contentTypeNames[fullscreenContent.content_type] || "未知"}
                  </span>
                  <span className="mr-2">|</span>
                  <span>
                    {new Date(fullscreenContent.created_at).toLocaleString()}
                  </span>
                </div>
              </div>
              <button 
                onClick={closeFullscreen}
                className="text-white hover:text-gray-200 focus:outline-none"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            
            <div className="flex-grow p-6 overflow-y-auto">
              {fullscreenContent.description && (
                <div className="mb-6 bg-purple-50 p-4 rounded-lg">
                  <h3 className="text-lg font-medium text-purple-800 mb-2">描述</h3>
                  <p className="text-gray-700">{fullscreenContent.description}</p>
                </div>
              )}
              
              <div className="bg-white border rounded-lg p-4">
                {fullscreenContent.content_detail && fullscreenContent.content_detail.content && (
                  <SimpleEditor
                    initialContent={fullscreenContent.content_detail.content.content}
                    readOnly={true}
                  />
                )}
              </div>
            </div>
            
            <div className="border-t p-4 bg-gray-50 flex justify-between items-center">
              <div className="flex space-x-6">
                <button
                  onClick={() => handleLike(fullscreenContent.id)}
                  className={`flex items-center ${
                    fullscreenContent.is_liked ? "text-purple-600" : "text-gray-600"
                  } hover:text-purple-800`}
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    className="h-5 w-5 mr-1"
                    fill={fullscreenContent.is_liked ? "currentColor" : "none"}
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={fullscreenContent.is_liked ? 0 : 1.5}
                      d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"
                    />
                  </svg>
                  <span>{fullscreenContent.likes_count || 0}</span>
                </button>
                
                <button
                  onClick={() => {
                    toggleComments(fullscreenContent.id);
                    closeFullscreen();
                  }}
                  className="flex items-center text-gray-600 hover:text-purple-800"
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    className="h-5 w-5 mr-1"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={1.5}
                      d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
                    />
                  </svg>
                  <span>查看评论 ({fullscreenContent.comments_count || 0})</span>
                </button>
              </div>
              
              <button
                onClick={closeFullscreen}
                className="px-4 py-2 bg-purple-600 text-white rounded hover:bg-purple-700 focus:outline-none"
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SharedContentList; 