const {ethers} = require ("ethers");
const provider = new ethers.providers.JsonRpcProvider('http://127.0.0.1:8545')

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

const main = async() => {

    const blockNumber = await provider.getBlockNumber()
    console.log(blockNumber)
    
}

main()

