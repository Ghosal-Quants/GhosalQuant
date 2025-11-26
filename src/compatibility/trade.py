BUY = 1
SELL = -1
NONE = None

class Trade:
    # minimal dummy Trade class
    def __init__(self, row):
        self.running = True # all new trades we can assume are open
        self.result = 0 # the result of the trade -> 1=win, -1=loss
        self.SIGNAL = row.SIGNAL

        # defined alsewhere in the strategy()
        self.TP = row.TP
        self.SL = row.SL
        self.entry_price = row.Close # test_backtester, test_strategy
        self.entry_time = row.name # test_get_data_script.py has ts as index

        self.exit_time = None
        self.exit_price = None
        self.pnl = None

        
    def update(self, row):
        """
        Some logic for checking if the current row is a winner or a lsoer
        """

        if self.SIGNAL == BUY:
            if row.High >= self.TP:
                # win
                self.close_trade(row, 1)
            elif row.Low <= self.SL:
                self.close_trade(row, -1)
                # loss
        if self.SIGNAL == SELL:
            if row.Low <= self.TP:
                # win
                self.close_trade(row, 1)
            elif row.High >= self.SL:
                # loss
                self.close_trade(row, -1)   

        pass

    def close_trade(self, row, result: int):
        self.running = False
        self.result = result

        # update when trade closes, prob have for exit price and time
        self.exit_time = row.Name # record ts when trade is closed

        # exit price as well
        if self.SIGNAL == BUY:
            if result == 1: # was a win
                self.exit_price = self.TP
            else:
                self.exit_price = self.SL
            
            self.pnl = self.exit_price - self.entry_price

        elif self.SIGNAL == SELL:
            if result == 1:
                self.exit_price = self.TP
            else:
                self.exit_price = self.SL
            
            self.pnl = self.exit_price - self.entry_price
