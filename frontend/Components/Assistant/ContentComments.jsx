import React, { useState, useEffect } from 'react';
import { getContentComments, createComment, deleteComment } from 'services/social.service';
import { useToasts } from "react-toast-notifications";

const ContentComments = ({ contentId }) => {
  const [comments, setComments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [commentText, setCommentText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [replyTo, setReplyTo] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  const { addToast } = useToasts();
  
  useEffect(() => {
    if (contentId) {
      fetchComments();
    }
  }, [contentId]);

  const fetchComments = async () => {
    try {
      setLoading(true);
      const response = await getContentComments(contentId);
      setComments(response.data || []);
    } catch (error) {
      console.error('获取评论失败', error);
      addToast("获取评论失败", { appearance: "error", autoDismiss: true });
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    if (!commentText.trim()) {
      addToast("请输入评论内容", { appearance: "error", autoDismiss: true });
      return;
    }
    
    try {
      setSubmitting(true);
      const data = {
        content: contentId,
        text: commentText.trim()
      };
      
      // 如果是回复评论，添加父评论ID
      if (replyTo) {
        data.parent = replyTo.id;
      }
      
      await createComment(data);
      addToast(replyTo ? '回复已提交' : '评论已提交', { appearance: 'success', autoDismiss: true });
      
      // 重置表单
      setCommentText('');
      setReplyTo(null);
      
      // 重新获取评论列表
      await fetchComments();
    } catch (error) {
      console.error('提交评论失败', error);
      addToast("提交评论失败", { appearance: "error", autoDismiss: true });
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id) => {
    if (!confirm('确定要删除此评论？')) {
      return;
    }
    
    try {
      setDeletingId(id);
      await deleteComment(id);
      addToast("评论已删除", { appearance: "success", autoDismiss: true });
      
      // 重新获取评论列表
      await fetchComments();
    } catch (error) {
      console.error('删除评论失败', error);
      addToast("删除评论失败", { appearance: "error", autoDismiss: true });
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div>
      <h4 className="text-lg font-medium mb-4">评论</h4>
      
      {/* 评论列表 */}
      {loading ? (
        <div className="text-center py-4">
          <div className="animate-spin inline-block w-6 h-6 border-4 border-gray-300 border-t-purple-600 rounded-full"></div>
          <p className="mt-2 text-sm text-gray-600">加载评论...</p>
        </div>
      ) : (
        <div className="space-y-4 mb-6">
          {comments.length > 0 ? (
            comments.map(comment => (
              <div key={comment.id} className="border-b pb-3 last:border-b-0">
                <div className="flex items-start">
                  <div className="h-8 w-8 bg-gray-300 rounded-full flex items-center justify-center text-gray-700 font-semibold">
                    {(comment.user_info?.username || '?')[0].toUpperCase()}
                  </div>
                  <div className="ml-3 flex-grow">
                    <div className="flex items-center justify-between">
                      <div className="font-medium">{comment.user_info?.username || '未知用户'}</div>
                      <div className="text-xs text-gray-500">{new Date(comment.created_at).toLocaleString()}</div>
                    </div>
                    <div className="text-gray-800 mt-1">{comment.text}</div>
                    
                    <div className="mt-2 flex items-center space-x-3">
                      <button
                        onClick={() => setReplyTo(comment)}
                        className="text-xs text-purple-600 hover:underline"
                      >
                        回复
                      </button>
                      
                      {/* 仅显示当前用户可删除自己的评论 */}
                      {comment.user === comment.user_info?.id && (
                        <button
                          onClick={() => handleDelete(comment.id)}
                          disabled={deletingId === comment.id}
                          className="text-xs text-red-600 hover:underline"
                        >
                          {deletingId === comment.id ? '删除中...' : '删除'}
                        </button>
                      )}
                    </div>
                    
                    {/* 显示回复 */}
                    {comment.replies && comment.replies.length > 0 && (
                      <div className="mt-3 pl-4 space-y-3 border-l-2 border-gray-200">
                        {comment.replies.map(reply => (
                          <div key={reply.id} className="flex items-start">
                            <div className="h-6 w-6 bg-gray-300 rounded-full flex items-center justify-center text-gray-700 text-xs font-semibold">
                              {(reply.user_info?.username || '?')[0].toUpperCase()}
                            </div>
                            <div className="ml-2 flex-grow">
                              <div className="flex items-center justify-between">
                                <div className="text-sm font-medium">{reply.user_info?.username || '未知用户'}</div>
                                <div className="text-xs text-gray-500">{new Date(reply.created_at).toLocaleString()}</div>
                              </div>
                              <div className="text-sm text-gray-800 mt-1">{reply.text}</div>
                              
                              {/* 仅显示当前用户可删除自己的评论 */}
                              {reply.user === reply.user_info?.id && (
                                <button
                                  onClick={() => handleDelete(reply.id)}
                                  disabled={deletingId === reply.id}
                                  className="mt-1 text-xs text-red-600 hover:underline"
                                >
                                  {deletingId === reply.id ? '删除中...' : '删除'}
                                </button>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))
          ) : (
            <div className="text-center py-4 text-gray-500">
              暂无评论，快来发表第一条评论吧！
            </div>
          )}
        </div>
      )}
      
      {/* 评论输入框 */}
      <form onSubmit={handleSubmit} className="mt-4">
        {replyTo && (
          <div className="flex items-center justify-between bg-purple-50 p-2 rounded-md mb-2">
            <div className="text-sm">
              回复 <span className="font-medium">{replyTo.user_info?.username}</span>
            </div>
            <button 
              type="button"
              onClick={() => setReplyTo(null)}
              className="text-sm text-gray-600 hover:text-gray-800"
            >
              取消回复
            </button>
          </div>
        )}
        
        <div className="flex items-start">
          <textarea
            value={commentText}
            onChange={(e) => setCommentText(e.target.value)}
            placeholder={replyTo ? "写下你的回复..." : "写下你的评论..."}
            rows={3}
            className="flex-grow border border-gray-300 rounded-md shadow-sm p-2 focus:outline-none focus:ring-purple-500 focus:border-purple-500"
            disabled={submitting}
          ></textarea>
          
          <button
            type="submit"
            disabled={submitting || !commentText.trim()}
            className="ml-2 px-4 py-2 bg-purple-600 text-white rounded hover:bg-purple-700 transition-colors duration-300 disabled:bg-purple-300"
          >
            {submitting ? '提交中...' : '提交'}
          </button>
        </div>
      </form>
    </div>
  );
};

export default ContentComments; 