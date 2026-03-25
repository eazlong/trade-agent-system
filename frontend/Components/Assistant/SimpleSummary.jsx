import { useToasts } from "react-toast-notifications";
import { useState, useEffect } from "react";
import { getSummary, createSummary } from "services/assistant.service";
import Editor from "./Editor";

const SimpleSummary = ({ record }) => {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showCreateButton, setShowCreateButton] = useState(false);

  const { addToast } = useToasts();
  const showToast = (message, appearance) => {
    addToast(message, { appearance });
  };

  useEffect(() => {
    if (!record || !record.order_id) {
      setSummary(null);
      setShowCreateButton(false);
      setLoading(false);
      return;
    }

    const fetchSummary = async () => {
      setLoading(true);
      setShowCreateButton(false);
      setSummary(null);
      try {
        console.log(record.order_id);
        
        const response = await getSummary(record.order_id);
        if (response.status === 200) {
          setSummary(response.data);
        }
      } catch (error) {
        console.log(error);
        
        if (error.response && error.response.status === 404) {
          setShowCreateButton(true);
        } else {
          showToast("获取失败:" + error.toString(), "error");
        }
      } finally {
        setLoading(false);
      }
    };

    fetchSummary();
    // eslint-disable-next-line
  }, [record]);

  const handleCreateSummary = async () => {
    if (!record || !record.order_id) return;
    setLoading(true);
    try {
      const response = await createSummary(record.order_id);
      if (response.status === 200) {
        setSummary(response.data);
        setShowCreateButton(false);
        showToast("新建复盘成功", "success");
      }
    } catch (error) {
      showToast("新建复盘失败:" + error.toString(), "error");
    } finally {
      setLoading(false);
    }
  };

  if (!record || !record.order_id) {
    return (
      <div className="flex items-center justify-center h-full w-full">
        <span className="text-gray-500">请选择一条交易记录</span>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full w-full">
        <span className="text-gray-500">加载中...</span>
      </div>
    );
  }

  if (summary) {
    return (
      <div className="h-full w-full">
        <Editor contentData={summary.content} editable={true}/>
      </div>
    );
  }

  if (showCreateButton) {
    return (
      <div className="flex items-center justify-center h-full w-full">
        <button
          className="px-6 py-2 bg-purple-600 text-white rounded shadow hover:bg-purple-700 focus:outline-none focus:ring-2 focus:ring-purple-400"
          onClick={handleCreateSummary}
          tabIndex={0}
          aria-label="新建复盘"
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") handleCreateSummary();
          }}
        >
          新建复盘
        </button>
      </div>
    );
  }

  return null;
};

export default SimpleSummary;