import API_URL, {apiClient} from 'services'

export const getUserBotConfigs = () => {
  return apiClient
    .get(API_URL + "bot/config/",)
    .then(async (configs) => await configs.data)
    // .catch(async res => await res.response);
};

export const addBotConfig = (data) => {
  return apiClient
    .post(API_URL + `bot/config/`, data)
    .then(async (templates) => await templates.data)
    // .catch(async res => await res.response);
};

export const updateBotConfig = (id, data) => {
  return apiClient
    .put(API_URL + `bot/config/${id}/`, data)
    .then(async (templates) => await templates.data)
    // .catch(async res => await res.response);
};

export const deleteBotConfig = (id) => {
  return apiClient
    .delete(API_URL + `bot/config/${id}/`)
    .then(async (templates) => await templates.data)
    // .catch(async res => await res.response);
};

// Trade record comments
export const getTradeRecordComments = (recordId) => {
  return apiClient.get(`/bot/trade-records/${recordId}/comments/`).then((response) => response.data);
};

export const createRecordComment = (data) => {
  return apiClient.post(`/bot/comments/`, data).then((response) => response.data);
};

export const deleteRecordComment = (commentId) => {
  return apiClient.delete(`/bot/comments/${commentId}/`).then((response) => response.data);
};

export const updateRecordComment = (commentId, data) => {
  return apiClient.put(`/bot/comments/${commentId}/`, data).then((response) => response.data);
};


export const getBotTradeRecord = (bot_id, symbol, page = 1, sort="") => {
  return apiClient
    .get(API_URL + `bot/${bot_id}/record/`, {
      params: { page: page, symbol: symbol, sort: sort},
    })
    .then(async (templates) => await templates.data);
    // .catch(async (res) => await res.response);
};

export const getTradeRecord = (order_id) => {
  return apiClient
    .get(API_URL + `bot/record/${order_id}/`,)
    .then(async (templates) => await templates.data);
    // .catch(async (res) => await res.response);
};

export const toggleBotStatus = (id, paused) => {
  return apiClient
    .put(API_URL + `bot/config/${id}/`, { paused })
    .then(async (response) => await response.data);
};