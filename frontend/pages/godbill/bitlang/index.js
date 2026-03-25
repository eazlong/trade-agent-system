import TradeRecords from "Components/Assistant/TradeRecords";
import TradeRecordComments from "Components/Assistant/TradeRecordComments";
import SimpleSummary from "Components/Assistant/SimpleSummary";
const GodBillPage = () => {
    
  return (
    <div className="h-full w-full">
      <TradeRecords
        who={'bitlang'}
        subWindow={{
          'title': "评论",
          'ui': TradeRecordComments, 
        }}
      />
    </div>
  );
};

export default GodBillPage;
