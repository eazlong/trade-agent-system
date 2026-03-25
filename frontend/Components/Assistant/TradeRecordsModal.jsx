import React, { useState, useEffect } from "react";
import {
  Modal,
  Box,
  Tabs,
  Title
} from "@mantine/core";
import TradeRecords from "./TradeRecords";
import TradeRecordComments from "./TradeRecordComments";

const TradeRecordsModal = ({
  open,
  handleClose,
  record = null,
  subWindow = null,
  who = null,
}) => {
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [activeTab, setActiveTab] = useState("chart");
  const [showComments, setShowComments] = useState(false);

  useEffect(() => {
    if (record) {
      setSelectedRecord(record);
      // If the record has bot_id > 900, enable comments tab
      if (record.bot_id > 900) {
        setShowComments(true);
      } else {
        setShowComments(false);
      }
    }
  }, [record]);

  const handleSelectRecord = (record) => {
    setSelectedRecord(record);
    // If the record has bot_id > 900, enable comments tab
    if (record.bot_id > 900) {
      setShowComments(true);
    } else {
      setShowComments(false);
    }
  };

  return (
    <Modal
      opened={open}
      onClose={handleClose}
      size="xl"
      title="交易记录"
      styles={{
        modal: { height: "90vh" },
        body: { height: "calc(90vh - 80px)", display: "flex", flexDirection: "column" }
      }}
    >
      {showComments && (
        <Tabs value={activeTab} onTabChange={setActiveTab} mb="md">
          <Tabs.List>
            <Tabs.Tab value="chart">交易图表</Tabs.Tab>
            <Tabs.Tab value="comments">评论</Tabs.Tab>
          </Tabs.List>
        </Tabs>
      )}
      
      <Box style={{ flex: 1, overflow: "hidden" }}>
        {showComments && activeTab === "comments" ? (
          <Box style={{ height: "100%", overflowY: "auto" }} p="md">
            <TradeRecordComments tradeRecord={selectedRecord} />
          </Box>
        ) : (
          <Box style={{ height: "100%" }}>
            <TradeRecords
              initRecord={record}
              onSelectRecord={handleSelectRecord}
              subWindow={subWindow}
              who={who}
            />
          </Box>
        )}
      </Box>
    </Modal>
  );
};

export default TradeRecordsModal;