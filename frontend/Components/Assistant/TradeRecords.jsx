// "use client";
import React, { useState, useRef, useEffect } from "react";
import { getBotTradeRecord } from "services/qtbot.service";
import {
  Button,
  Box,
  Text,
  Loader,
  Divider,
  Tabs,
  Paper,
  TextInput,
  ActionIcon,
  Stack,
  ScrollArea,
  Group
} from "@mantine/core";
import { useMediaQuery } from '@mantine/hooks';
import { IconArrowsSort } from '@tabler/icons-react';
import { side } from "Utils/order";
import TView from "./TView";
import { getTradeRecords } from "../../services/trade.service";
import { useSelector } from "react-redux";

const TradeRecords = ({ onSelectRecord, subWindow = null, initRecord = null, who = null }) => {
  const isMobile = useMediaQuery('(max-width: 768px)');
  const [selectedTrade, setSelectedTrade] = useState(null);
  const [tradeRecords, setTradeRecords] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [activeTab, setActiveTab] = useState('list');
  const [showSubWindow, setShowSubWindow] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [sortOrder, setSortOrder] = useState("desc");
  const firstSearchEffectRun = useRef(true);

  const listRef = useRef(null);
  const viewportRef = useRef(null);
  const sidebar = useSelector((state) => state.sidebar);

  useEffect(() => {
    setSelectedTrade(pre => {
      if (pre) {
        return pre;
      }
      return null;
    });
  }, [sidebar]);

  useEffect(() => {
    if (!searchTerm.endsWith("USDT") && !searchTerm.endsWith("usdt") && searchTerm.length > 0) {
      return;
    }

    if (initRecord) {
      console.log("onSelectTrade selected:", initRecord);

      setTradeRecords([initRecord]);
      handleSelectTrade(initRecord);
      if (isMobile) setActiveTab('chart');
    } else {
      setPage(1);
      setTradeRecords([]);
      fetchTradeRecords(1, searchTerm, sortOrder);
    }
  }, [initRecord, isMobile, searchTerm, sortOrder]);

  const fetchTradeRecords = async (page, search, sort) => {
    if (isLoading) return;
    setIsLoading(true);
    try {
      let bot_id = who == "bitlang" ? -999 : -1;
      const sortParam = sort === 'desc' ? '-timestamp' : 'timestamp';
      const response = await getBotTradeRecord(
        bot_id,
        search,
        page,
        sortParam
      );
      if (response.results.length > 0) {
        setTradeRecords((prevRecords) =>
          page === 1 ? response.results : [...prevRecords, ...response.results]
        );
        setHasMore(true);
        setPage(page + 1);
      } else {
        setHasMore(false);
        if (page === 1) {
          setTradeRecords([]);
        }
      }
    } catch (error) {
      console.error("Error fetching trade records:", error);
      if (error.response && error.response?.status == 404) {
        setHasMore(false);
      }
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!searchTerm.endsWith("USDT") && !searchTerm.endsWith("usdt")) {
      return;
    }
    
    if (firstSearchEffectRun.current) {
      firstSearchEffectRun.current = false;
      return;
    }
    const timer = setTimeout(() => {
      setPage(1);
      setTradeRecords([]);
      setHasMore(true);
      fetchTradeRecords(1, searchTerm, sortOrder);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchTerm]);

  const handleSelectTrade = (trade) => {
    setShowSubWindow(true);
    console.log("Trade selected:", trade);
    if (trade?.parent_order) {
      for (let t of trade?.parent_order) {
        t.opt = side(t);
      }
    }
    console.log("onSelectTrade selected:", trade);
    onSelectRecord && onSelectRecord(trade);
    setSelectedTrade(trade);
    if (isMobile) {
      setActiveTab('chart');
    }
  };

  const handleSort = () => {
    setSortOrder(prevOrder => prevOrder === 'asc' ? 'desc' : 'asc');
  };

  const renderTradeItem = (record, index) => {
    const tradeColor = side(record) === "开空" || side(record) === "平空" ? "#ef5350" : "#26a69a";
    
    return (
      <Box
        key={index}
        onClick={() => handleSelectTrade(record)}
        style={{
          padding: '12px 16px',
          cursor: 'pointer',
          backgroundColor: selectedTrade?.id === record.id ? 'rgba(124, 58, 237, 0.1)' : 'transparent',
        }}
        className="hover:bg-gray-50"
      >
        <Text
          style={{
            color: tradeColor,
            marginBottom: '4px',
            fontWeight: 'bold',
          }}
        >
          {isMobile 
            ? `${record.symbol} - ${side(record)}`
            : `${record.symbol} - ${record.leverage !== "0.00000" ? Intl.NumberFormat('en-US').format(record.leverage) + "x" : ""} ${side(record)}`
          }
        </Text>
        <Text size="sm" c="dimmed">
          {isMobile ? (
            <>
              价格: {record.price} | 数量: {record.amount}
              <br />
              {new Date(record.timestamp).toLocaleString()}
            </>
          ) : (
            <>
              Price: {record.price} | Total: {(record.amount * record.price).toFixed(0)} USDT
              <br />
              Profit: <Text component="span" style={{ color: record.profit > 0 ? "#26a69a" : "#ef5350" }}>{Number(record.profit).toFixed(2)} USDT</Text>
              {record.profit_pct != '0.00000' && (
                <>
                  {" | Pct: "}
                  <Text component="span" style={{ color: record.profit_pct > 0 ? "#26a69a" : "#ef5350" }}>
                    {Number(record.profit_pct).toFixed(2)}%
                  </Text>
                </>
              )}
              <br />
              {new Date(record.timestamp).toLocaleString()}
            </>
          )}
        </Text>
      </Box>
    );
  };

  const renderTradeList = () => {
    const handleScroll = (e) => {
      const { scrollTop, scrollHeight, clientHeight } = e.target;
      
      // console.log("Scroll position:", { scrollTop, scrollHeight, clientHeight, remaining: scrollHeight - scrollTop - clientHeight });
      
      // Check if we're within 100px of the bottom
      if (
        scrollHeight - scrollTop - clientHeight < 100 &&
        hasMore &&
        !isLoading &&
        tradeRecords.length > 0 // Make sure we have some records
      ) {
        console.log("Loading more records, page:", page);
        fetchTradeRecords(page, searchTerm, sortOrder);
      }
    };

    return (
      <Stack spacing={0} style={{ height: '100%' }}>
        <Group p="sm" style={{ backgroundColor: 'white' }}>
          <TextInput
            style={{ flex: 1 }}
            placeholder="搜索币种..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
          <ActionIcon onClick={handleSort}>
            <IconArrowsSort 
              style={{ transform: sortOrder === 'asc' ? 'rotate(180deg)' : 'none' }} 
            />
          </ActionIcon>
        </Group>
        
        <div
          ref={viewportRef}
          onScroll={handleScroll}
          style={{
            flex: 1,
            overflow: 'auto',
            height: '100%'
          }}
        >
          <Stack spacing={0}>
            {tradeRecords.map((record, index) => (
              <React.Fragment key={index}>
                {renderTradeItem(record, index)}
                {index < tradeRecords.length - 1 && <Divider />}
              </React.Fragment>
            ))}
            {isLoading && (
              <Box style={{ display: 'flex', justifyContent: 'center', padding: '16px' }}>
                <Loader size={24} />
              </Box>
            )}
            {!hasMore && (
              <Box style={{ textAlign: 'center', padding: '16px' }}>
                <Text c="dimmed">没有更多数据了</Text>
              </Box>
            )}
          </Stack>
        </div>
      </Stack>
    );
  };

  const renderChart = () => {
    return selectedTrade ? (
      <Box style={{ width: '100%', height: '100%' }}>
        <TView order={{ ...selectedTrade, opt: side(selectedTrade) }} />
      </Box>
    ) : (
      <Box style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'center', 
        height: '100%',
        backgroundColor: 'rgba(124, 58, 237, 0.1)'
      }}>
        <Text c="dimmed">请选择一条交易记录查看图表</Text>
      </Box>
    );
  };

  // 移动端显示
  const renderMobileView = () => {
    return (
      <Box style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <Tabs value={activeTab} onChange={setActiveTab}>
          <Tabs.List grow>
            <Tabs.Tab value="list">交易列表</Tabs.Tab>
            <Tabs.Tab value="chart" disabled={!selectedTrade}>图表</Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="list" style={{ flex: 1, overflow: 'hidden' }}>
            {renderTradeList()}
          </Tabs.Panel>

          <Tabs.Panel value="chart" style={{ flex: 1, overflow: 'hidden' }}>
            {renderChart()}
          </Tabs.Panel>
        </Tabs>
      </Box>
    );
  };

  // 桌面端显示
  const renderDesktopView = () => {
    return (
      <Box style={{ height: '100%', display: 'flex' }}>
        <Box style={{ width: '20%', borderRight: '1px solid #e9ecef', backgroundColor: 'rgba(124, 58, 237, 0.1)' }}>
          {renderTradeList()}
        </Box>
        
        <Box style={{ flex: 1, position: 'relative' }}>
          {showSubWindow && selectedTrade && subWindow ? (
            <Box style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
              <Box style={{ flex: 1, borderBottom: '1px solid #e9ecef' }}>
                <TView order={{ ...selectedTrade, opt: side(selectedTrade) }} />
              </Box>
              <Box style={{ flex: 1, overflow: 'auto' }}>
                {subWindow?.ui && <subWindow.ui record={selectedTrade} />}
              </Box>
            </Box>
          ) : (
            renderChart()
          )}
        </Box>
      </Box>
    );
  };

  return isMobile ? renderMobileView() : renderDesktopView();
};

export default TradeRecords;
