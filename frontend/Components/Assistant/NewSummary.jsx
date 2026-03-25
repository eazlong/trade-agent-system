import { useState, useEffect } from "react";
import TradeRecords from "Components/Assistant/TradeRecords";
import SimpleSummary from "Components/Assistant/SimpleSummary";

const NewSummary = ({ initRecord = null }) => {
  const [selectedRecord, setSelectedRecord] = useState(initRecord);

  useEffect(() => {
    setSelectedRecord(initRecord);
  }, [initRecord]);

  return selectedRecord ? (
    <div className="h-full w-full">
      <TradeRecords
        initRecord={selectedRecord}
        subWindow={{ title: "复盘", ui: SimpleSummary }}
      />
    </div>
  ) : (
    <div className="h-full w-full">
      <TradeRecords subWindow={{ title: "复盘", ui: SimpleSummary }} />
    </div>
  );
};

export default NewSummary;
