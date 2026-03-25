import API_URL, { apiClient } from "services";

const analyzeSystem = async () => {
  const response = await apiClient
    .get(API_URL + "ai/analyze-system/")
    .then(async (res) => await res);
  return response;
};

const analyzePlan = async (plan_id) => {
  const response = await apiClient
    .get(API_URL + "ai/analyze-plan/", { params: { plan_id: plan_id } }, {timeout: 300000})
    .then(async (res) => await res);
  return response;
};

export { analyzeSystem, analyzePlan };