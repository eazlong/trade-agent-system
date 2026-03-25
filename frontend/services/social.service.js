import axios from "axios";
import API_URL, { apiClient } from "services";

// 邀请码相关
const getUserInvite = async () => {
  const response = await apiClient
    .get(API_URL + "assistant/invite/")
    .then(async (res) => await res);
  return response;
};

const regenerateInvite = async () => {
  const response = await apiClient
    .post(API_URL + "assistant/invite/")
    .then(async (res) => await res);
  return response;
};

// 好友请求相关
const sendFriendRequest = async (data) => {
  const response = await apiClient
    .post(API_URL + "assistant/friend-requests/", data)
    .then(async (res) => await res);
  return response;
};

const getFriendRequests = async () => {
  const response = await apiClient
    .get(API_URL + "assistant/friend-requests/")
    .then(async (res) => await res);
  return response;
};

const getReceivedFriendRequests = async () => {
  const response = await apiClient
    .get(API_URL + "assistant/friend-requests/received/")
    .then(async (res) => await res);
  return response;
};

const getSentFriendRequests = async () => {
  const response = await apiClient
    .get(API_URL + "assistant/friend-requests/sent/")
    .then(async (res) => await res);
  return response;
};

const acceptFriendRequest = async (id) => {
  const response = await apiClient
    .post(API_URL + `assistant/friend-requests/${id}/accept/`)
    .then(async (res) => await res);
  return response;
};

const rejectFriendRequest = async (id) => {
  const response = await apiClient
    .post(API_URL + `assistant/friend-requests/${id}/reject/`)
    .then(async (res) => await res);
  return response;
};

// 好友相关
const getFriends = async () => {
  const response = await apiClient
    .get(API_URL + "assistant/friends/")
    .then(async (res) => await res);
  return response;
};

const deleteFriend = async (id) => {
  const response = await apiClient
    .delete(API_URL + `assistant/friends/${id}/`)
    .then(async (res) => await res);
  return response;
};

// 分享内容相关
const getSharedContents = async () => {
  const response = await apiClient
    .get(API_URL + "assistant/shared-contents/")
    .then(async (res) => await res);
  return response;
};

const getMySharedContents = async () => {
  const response = await apiClient
    .get(API_URL + "assistant/shared-contents/mine/")
    .then(async (res) => await res);
  return response;
};

const getFriendsSharedContents = async () => {
  const response = await apiClient
    .get(API_URL + "assistant/shared-contents/friends/")
    .then(async (res) => await res);
  return response;
};

const getPublicSharedContents = async () => {
  const response = await apiClient
    .get(API_URL + "assistant/shared-contents/public/")
    .then(async (res) => await res);
  return response;
};

const getSharedContent = async (id) => {
  const response = await apiClient
    .get(API_URL + `assistant/shared-contents/${id}/`)
    .then(async (res) => await res);
  return response;
};

const createSharedContent = async (data) => {
  const response = await apiClient
    .post(API_URL + "assistant/shared-contents/", data)
    .then(async (res) => await res);
  return response;
};

const updateSharedContent = async (id, data) => {
  const response = await apiClient
    .patch(API_URL + `assistant/shared-contents/${id}/`, data)
    .then(async (res) => await res);
  return response;
};

const deleteSharedContent = async (id) => {
  const response = await apiClient
    .delete(API_URL + `assistant/shared-contents/${id}/`)
    .then(async (res) => await res);
  return response;
};

// 评论相关
const getContentComments = async (contentId) => {
  const response = await apiClient
    .get(API_URL + `assistant/content-comments/?content_id=${contentId}`)
    .then(async (res) => await res);
  return response;
};

const createComment = async (data) => {
  const response = await apiClient
    .post(API_URL + "assistant/content-comments/", data)
    .then(async (res) => await res);
  return response;
};

const deleteComment = async (id) => {
  const response = await apiClient
    .delete(API_URL + `assistant/content-comments/${id}/`)
    .then(async (res) => await res);
  return response;
};

// 点赞相关
const toggleContentLike = async (contentId) => {
  const response = await apiClient
    .post(API_URL + "assistant/content-likes/", { content: contentId })
    .then(async (res) => await res);
  return response;
};

const getContentLikes = async (contentId) => {
  const response = await apiClient
    .get(API_URL + `assistant/content-likes/?content_id=${contentId}`)
    .then(async (res) => await res);
  return response;
};

export {
  getUserInvite,
  regenerateInvite,
  sendFriendRequest,
  getFriendRequests,
  getReceivedFriendRequests,
  getSentFriendRequests,
  acceptFriendRequest,
  rejectFriendRequest,
  getFriends,
  deleteFriend,
  getSharedContents,
  getMySharedContents,
  getFriendsSharedContents,
  getPublicSharedContents,
  getSharedContent,
  createSharedContent,
  updateSharedContent,
  deleteSharedContent,
  getContentComments,
  createComment,
  deleteComment,
  toggleContentLike,
  getContentLikes
}; 