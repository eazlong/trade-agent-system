import axios from "axios";
import API_URL, {apiClient} from "services";

const getHotCoin = async () => {
  const response = await axios
    .get(API_URL + "assistant/hotcoin/")
    .then(async (res) => await res);
  return response;
};

const postEditorContent = async (content) => {
  const response = await apiClient
    .post(API_URL + "assistant/editor-content/", { content })
    .then(async (res) => await res);
  return response;
}; 

const getEditorContent = async (id) => {
  const response = await apiClient
    .get(API_URL + "assistant/editor-content/" + id + "/")
    .then(async (res) => await res);
  return response;
};

const updateEditorContent = async (id, content) => {
  const response = await apiClient
    .put(API_URL + "assistant/editor-content/" + id + "/", { content })
    .then(async (res) => await res);
  return response;
};

const getPlan = async (symbol, date=null) => { 
  const response = await apiClient
    .get(API_URL + "assistant/plan/" + symbol + "/" + (date ? "?date=" + date : ""))
    .then(async (res) => await res);
  return response;
};

const getAllPlan = async (date = null, page = 1) => {
  const response = await apiClient
    .get(API_URL + "assistant/plan/" + (date ? "?date=" + date : ""), {
      params: {
        page: page
      }
    })
    .then(async (res) => await res);
  return response;
};

const createPlan = async (symbol, data) => {  
  const response = await apiClient
    .post(API_URL + "assistant/plan/?symbol=" + symbol, data)
    .then(async (res) => await res);
  return response;
};

const deletePlan = async (id) => {
  const response = await apiClient
    .delete(API_URL + "assistant/plan/" + id + "/")
    .then(async (res) => await res);
  return response;
};

const uploadScreenshot = async (dataUrl) => {
  const response = await apiClient
    .post(API_URL + "assistant/upload-screenshot/", { image: dataUrl })
    .then(async (res) => await res);
  return response;
};

const getSummaries = async (who = null) => {
  const response = await apiClient
    .get(API_URL + "assistant/trade-summary/" + (who ? "?who=" + who : ""))
    .then(async (res) => await res);
  return response;
};

const getSummary = async (orderId) => {
  const response = await apiClient
    .get(API_URL + "assistant/trade-summary/" + orderId + "/")
    .then(async (res) => await res);
  return response;
};

const createSummary = async (order_id) => {
  const response = await apiClient
    .post(API_URL + "assistant/trade-summary/?order_id=" + order_id, {})
    .then(async (res) => await res);
  return response;
};

const deleteSummary = async (id) => {
  const response = await apiClient
    .delete(API_URL + "assistant/trade-summary/" + id + "/")
    .then(async (res) => await res);
  return response;
};

const getCondition = async (condition_id) => {
  const response = await apiClient
    .get(API_URL + "assistant/trade-condition/" + condition_id + "/")
    .then(async (res) => await res);
  return response;
};

const updateCondition = async (condition_id, data) => {
  const response = await apiClient
    .put(API_URL + "assistant/trade-condition/" + condition_id + "/", data)
    .then(async (res) => await res);
  return response;
};

const createCondition = async (condition_id) => {
  const response = await apiClient
    .post(API_URL + "assistant/trade-condition/?condition_id=" + condition_id, {})
    .then(async (res) => await res);
  return response;
};

const getTradingSystem = async () => {
  const response = await apiClient
    .get(API_URL + "assistant/trade-system/" )
    .then(async (res) => await res);
  return response;
};

const createTradingSystem = async () => {
  const response = await apiClient
    .post(API_URL + "assistant/trade-system/" )
    .then(async (res) => await res);
  return response;
};

const getEditorTemplates = async (category = null) => {
  const url = category 
    ? `${API_URL}assistant/editor-templates/?category=${category}`
    : `${API_URL}assistant/editor-templates/`;
  
  const response = await apiClient
    .get(url)
    .then(async (res) => await res);
  return response;
};

const getEditorTemplate = async (id) => {
  const response = await apiClient
    .get(`${API_URL}assistant/editor-templates/${id}/`)
    .then(async (res) => await res);
  return response;
};

const createEditorTemplate = async (data) => {
  const response = await apiClient
    .post(`${API_URL}assistant/editor-templates/`, data)
    .then(async (res) => await res);
  return response;
};

const updateEditorTemplate = async (id, data) => {
  const response = await apiClient
    .put(`${API_URL}assistant/editor-templates/${id}/`, data)
    .then(async (res) => await res);
  return response;
};

const deleteEditorTemplate = async (id) => {
  const response = await apiClient
    .delete(`${API_URL}assistant/editor-templates/${id}/`)
    .then(async (res) => await res);
  return response;
};
const getCommonData = async () => {
  const response = await apiClient
    .get(`${API_URL}assistant/common-data/`)
    .then(async (res) => await res);
  return response;
};

const getUserLines = async (order_id, config = {}) => {
  const response = await apiClient
    .get(`${API_URL}assistant/user-lines/${order_id}/`, config)
    .then(async (res) => await res);
  return response;
};

const createUserLines = async (data) => {
  const response = await apiClient
    .post(`${API_URL}assistant/user-lines/`, data)
    .then(async (res) => await res);
  return response;
};

const updateUserLines = async (id, data) => { 
  const response = await apiClient
    .put(`${API_URL}assistant/user-lines/${id}/`, data)
    .then(async (res) => await res);
  return response;
};

const deleteUserLines = async (id) => { 
  const response = await apiClient
    .delete(`${API_URL}assistant/user-lines/${id}/`)
    .then(async (res) => await res);
  return response;
};

export {
  getHotCoin,
  postEditorContent,
  updateEditorContent,
  getEditorContent,
  getPlan,
  getAllPlan,
  createPlan,
  deletePlan,
  getSummary, 
  getSummaries,
  createSummary,
  deleteSummary,
  uploadScreenshot,
  getCondition,
  updateCondition,
  createCondition,
  getTradingSystem,
  createTradingSystem,
  getEditorTemplates,
  getEditorTemplate,
  createEditorTemplate,
  updateEditorTemplate,
  deleteEditorTemplate,
  getCommonData,
  getUserLines,
  createUserLines,
  updateUserLines,
  deleteUserLines
};