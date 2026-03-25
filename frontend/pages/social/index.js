import React, { useState } from 'react';
import Layout from 'Layout';
import InviteCode from 'Components/Assistant/InviteCode';
import FriendRequest from 'Components/Assistant/FriendRequest';
import FriendsList from 'Components/Assistant/FriendsList';
import SharedContentList from 'Components/Assistant/SharedContentList';
import SectionTitle from 'Components/UI/SectionTitle';

export default function SocialPage() {
  const [activeTab, setActiveTab] = useState('content'); // 'content', 'friends', 'requests'
  
  return (
      <div className="container mx-auto px-4 py-8">
        {/* <SectionTitle title="社交中心" /> */}
        
        <div className="flex border-b mb-6">
          <button
            onClick={() => setActiveTab('content')}
            className={`px-4 py-2 ${
              activeTab === 'content'
                ? 'border-b-2 border-purple-500 text-purple-600 -mb-px'
                : 'text-gray-600'
            }`}
          >
            交易内容分享
          </button>
          <button
            onClick={() => setActiveTab('friends')}
            className={`px-4 py-2 ${
              activeTab === 'friends'
                ? 'border-b-2 border-purple-500 text-purple-600 -mb-px'
                : 'text-gray-600'
            }`}
          >
            好友管理
          </button>
          <button
            onClick={() => setActiveTab('requests')}
            className={`px-4 py-2 ${
              activeTab === 'requests'
                ? 'border-b-2 border-purple-500 text-purple-600 -mb-px'
                : 'text-gray-600'
            }`}
          >
            好友请求
          </button>
          <button
            onClick={() => setActiveTab('invite')}
            className={`px-4 py-2 ${
              activeTab === 'invite'
                ? 'border-b-2 border-purple-500 text-purple-600 -mb-px'
                : 'text-gray-600'
            }`}
          >
            邀请码
          </button>
        </div>
        
        <div className="py-4">
          {activeTab === 'content' && <SharedContentList />}
          {activeTab === 'friends' && <FriendsList />}
          {activeTab === 'requests' && <FriendRequest />}
          {activeTab === 'invite' && <InviteCode />}
        </div>
      </div>
  );
} 