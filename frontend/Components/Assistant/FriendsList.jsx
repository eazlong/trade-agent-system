import React, { useState, useEffect } from 'react';
import { getFriends, deleteFriend } from 'services/social.service';
import { useToasts } from "react-toast-notifications";

const FriendsList = () => {
  const [friends, setFriends] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState(null);
  const { addToast } = useToasts();
  useEffect(() => {
    fetchFriends();
  }, []);

  const fetchFriends = async () => {
    try {
      setLoading(true);
      const response = await getFriends();
      setFriends(response.data || []);
    } catch (error) {
      console.error('获取好友列表失败', error);
      addToast("获取好友列表失败", { appearance: "error", autoDismiss: true });
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteFriend = async (id) => {
    if (!confirm('确定要删除这位好友吗？')) {
      return;
    }
    
    try {
      setDeletingId(id);
      await deleteFriend(id);
      
      // 更新好友列表
      setFriends(friends.filter(friend => friend.id !== id));
      addToast("好友已删除", { appearance: "success", autoDismiss: true });
    } catch (error) {
      console.error('删除好友失败', error);
      addToast("删除好友失败", { appearance: "error", autoDismiss: true });
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="bg-purple-100 shadow-md rounded-lg p-5">
      <h2 className="text-xl font-semibold text-gray-800 mb-4">我的好友</h2>
      
      {loading ? (
        <div className="text-center py-8">
          <div className="animate-spin inline-block w-8 h-8 border-4 border-gray-300 border-t-purple-600 rounded-full"></div>
          <p className="mt-2 text-gray-600">加载中...</p>
        </div>
      ) : friends.length > 0 ? (
        <div className="divide-y">
          {friends.map((friend) => (
            <div key={friend.id} className="py-3">
              <div className="flex justify-between items-center">
                <div className="flex items-center">
                  <div className="h-10 w-10 bg-gray-300 rounded-full flex items-center justify-center text-gray-700 font-semibold text-lg">
                    {(friend.friend_info?.username || '?')[0].toUpperCase()}
                  </div>
                  <div className="ml-3">
                    <div className="font-medium">{friend.friend_info?.username || '未知用户'}</div>
                    <div className="text-sm text-gray-500">好友添加时间: {new Date(friend.created_at).toLocaleDateString()}</div>
                  </div>
                </div>
                <button
                  onClick={() => handleDeleteFriend(friend.id)}
                  disabled={deletingId === friend.id}
                  className="px-3 py-1 bg-red-600 text-white rounded hover:bg-red-700 transition-colors duration-300 disabled:bg-red-300"
                >
                  {deletingId === friend.id ? '处理中...' : '删除'}
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-8 text-gray-500">
          你还没有添加好友
        </div>
      )}
    </div>
  );
};

export default FriendsList; 