import React, { useEffect, useState } from "react";
import { Box, Text, Loader, Button, Modal, Title, ScrollArea, Group } from "@mantine/core";
import Editor from "Components/Assistant/Editor";
import { getTradingSystem, createTradingSystem } from "services/assistant.service";
import ShareContent from "./ShareContent";
import { analyzeSystem } from "services/ai.service";

const TradingSystem = () => {
  const [system, setSystem] = useState(null);
  const [loading, setLoading] = useState(true);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [openAnalysisDialog, setOpenAnalysisDialog] = useState(false);
  const [analysisResult, setAnalysisResult] = useState("");

  useEffect(() => {
    const fetchContentData = async () => {
      try {
        const response = await getTradingSystem();
        setSystem(response.data[0]);
      } catch (error) {
        console.error("Error fetching content data:", error);
        if (error.response?.status == 404) {
          const response = await createTradingSystem();
          setSystem(response.data);
        }
      } finally {
        setLoading(false);
      }
    };

    fetchContentData();
  }, []);

  const handleAnalyzeClick = async () => {
    setAnalysisLoading(true);
    try {
      const response = await analyzeSystem();
      if (response.status == 200) {
        setAnalysisResult(response.data?.message?.raw || response.data?.message || response.message);
        setOpenAnalysisDialog(true);
      } else {
        setAnalysisResult("AI 分析失败，请稍后再试。");
        setOpenAnalysisDialog(true);
      }
    } catch (error) {
      console.error("Error during AI analysis:", error);
      setAnalysisResult("AI 分析失败，请稍后再试。");
      setOpenAnalysisDialog(true);
    } finally {
      setAnalysisLoading(false);
    }
  };

  const handleCloseAnalysisDialog = () => {
    setOpenAnalysisDialog(false);
    setAnalysisResult("");
  };

  if (loading) {
    return (
      <Box style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', width: '100%' }}>
        <Loader />
      </Box>
    );
  }

  return (
    <Box
      className="w-full p-1 lg:p-4 mb-2 overflow-auto"
      style={{ maxHeight: "calc(100vh - 100px)" }}
    >
      {system && (
        <Group position="apart" style={{ marginBottom: '16px' }}>
          <Button variant="outline"
            onClick={handleAnalyzeClick}
            disabled={analysisLoading}
            style={{ marginLeft: 'auto', border: 'none', fontWeight: 'normal' }}
          >
            {analysisLoading ? <Loader size={16} /> : "AI 分析"}
          </Button>
          <ShareContent
            contentType="system"
            contentId={system.id}
            title="我的交易系统"
          />
        </Group>
      )}
      <Editor contentData={system?.content} editable={true} />

      <Modal
        opened={openAnalysisDialog}
        onClose={handleCloseAnalysisDialog}
        size="lg"
        title="AI 分析结果"
      >
        <ScrollArea style={{ maxHeight: '400px' }}>
          <Text style={{ whiteSpace: "pre-wrap" }}>
            {analysisResult}
          </Text>
        </ScrollArea>
        <Group position="right" style={{ marginTop: '16px' }}>
          <Button onClick={handleCloseAnalysisDialog}>关闭</Button>
        </Group>
      </Modal>
    </Box>
  );
};

export default TradingSystem;
